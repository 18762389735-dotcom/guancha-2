(function (global) {
  'use strict';

  const JPEG = 'image/jpeg';
  const PNG = 'image/png';
  const IOS_HEIF_TYPES = new Set(['image/heic', 'image/heif']);

  function extensionType(name) {
    const suffix = String(name || '').toLowerCase().match(/\.([a-z0-9]+)$/)?.[1];
    if (suffix === 'jpg' || suffix === 'jpeg') return JPEG;
    if (suffix === 'png') return PNG;
    if (suffix === 'heic') return 'image/heic';
    if (suffix === 'heif') return 'image/heif';
    return '';
  }

  function detectedType(file) {
    return String(file?.type || '').toLowerCase() || extensionType(file?.name);
  }

  function result(ok, details) {
    return { ok, files: [], converted: false, code: null, message: '', ...details };
  }

  function decodeImage(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const image = new Image();
      image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
      image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('image_decode_failed')); };
      image.src = url;
    });
  }

  function canvasBlob(canvas, quality) {
    return new Promise((resolve) => canvas.toBlob(resolve, JPEG, quality));
  }

  function normalizedName(file) {
    const base = String(file.name || 'product-image').replace(/\.[a-z0-9]+$/i, '');
    return `${base || 'product-image'}.jpg`;
  }

  async function convertToSafeJpeg(file, maximumBytes) {
    const image = await decodeImage(file);
    let width = image.naturalWidth || image.width;
    let height = image.naturalHeight || image.height;
    if (!width || !height) throw new Error('image_decode_failed');

    for (let pass = 0; pass < 6; pass += 1) {
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('canvas_unavailable');
      context.drawImage(image, 0, 0, width, height);
      for (const quality of [0.9, 0.8, 0.7, 0.6]) {
        const blob = await canvasBlob(canvas, quality);
        if (blob && blob.size > 0 && blob.size <= maximumBytes) {
          return new File([blob], normalizedName(file), { type: JPEG, lastModified: Date.now() });
        }
      }
      width = Math.max(1, Math.round(width * 0.78));
      height = Math.max(1, Math.round(height * 0.78));
    }
    throw new Error('image_too_large_after_compression');
  }

  function normalizedNativeFile(file, type) {
    if (file.type === type) return file;
    return new File([file], file.name || `product-image.${type === PNG ? 'png' : 'jpg'}`, { type, lastModified: file.lastModified || Date.now() });
  }

  async function prepareOne(file, limits) {
    if (!file || !Number.isFinite(file.size) || file.size <= 0) {
      return result(false, { code: 'empty_image', message: '图片为空，请重新选择截图或 JPEG/PNG 图片' });
    }
    const type = detectedType(file);
    const allowed = new Set(limits.allowedImageMimeTypes || [JPEG, PNG]);
    const knownNative = allowed.has(type);
    const isHeif = IOS_HEIF_TYPES.has(type);

    if (!knownNative && !isHeif) {
      return result(false, { code: 'invalid_image_type', message: '仅支持 JPEG 或 PNG 图片；请从相册选择截图或 JPEG/PNG 图片' });
    }

    if (knownNative && file.size <= limits.maxImageBytes) {
      return result(true, { files: [normalizedNativeFile(file, type)] });
    }

    try {
      const converted = await convertToSafeJpeg(file, limits.maxImageBytes);
      return result(true, { files: [converted], converted: true });
    } catch (error) {
      if (isHeif) {
        return result(false, { code: 'heif_not_decodable', message: '此相册图片为 HEIC/HEIF，当前浏览器无法转换，请选择截图或 JPEG/PNG 图片' });
      }
      return result(false, { code: error?.message === 'image_too_large_after_compression' ? 'image_too_large' : 'image_decode_failed', message: error?.message === 'image_too_large_after_compression' ? '图片压缩后仍超过 5MB，请选择更小的截图' : '图片无法解码，请重新选择截图或 JPEG/PNG 图片' });
    }
  }

  async function prepareFiles(files, options) {
    const list = Array.from(files || []);
    const limits = {
      allowedImageMimeTypes: options?.allowedImageMimeTypes || [JPEG, PNG],
      maxImageBytes: options?.maxImageBytes || 5 * 1024 * 1024,
    };
    const remaining = Number.isFinite(options?.remaining) ? options.remaining : list.length;
    if (!list.length) return result(false, { code: 'no_image_selected', message: '请选择一张截图或 JPEG/PNG 图片' });
    if (list.length > remaining) return result(false, { code: 'image_limit_exceeded', message: `最多可添加 ${remaining} 张图片` });

    const prepared = [];
    let converted = false;
    for (const file of list) {
      const item = await prepareOne(file, limits);
      if (!item.ok) return item;
      prepared.push(...item.files);
      converted = converted || item.converted;
    }
    return result(true, { files: prepared, converted });
  }

  global.GuanchaImagePreparation = Object.freeze({ prepareFiles, detectedType, extensionType });
}(window));
