const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..', '..');

function exists(relativePath) {
  return fs.existsSync(path.join(root, relativePath));
}

test('current UI assets are present in the Docker build context', () => {
  const required = [
    'assets/ui/AI分析等待插画.svg',
    'assets/ui/候选空状态.png',
    'assets/ui/选茶空状态.png',
    'assets/ui/候选茶非空状态.png',
    'assets/ui/选茶非空状态.png',
    'assets/ui/泡茶插画.svg',
    'assets/ui/茶迹插画.svg',
    'assets/ui/art-bag-clean.png',
    'assets/ui/art-can-clean.png',
    'assets/ui/art-cup-clean.png',
    'assets/ui/art-gaiwan-clean.png',
    'assets/o1-category-icons/tea.png',
    'assets/o1-category-icons/coffee.png',
    'assets/o1-category-icons/milk.png',
    'assets/o1-category-icons/juice.png',
    'assets/flavors-normalized/茉莉花.png',
    'assets/flavors-normalized/兰花.png',
    'assets/flavors-normalized/桂花.png',
    'assets/flavors-normalized/玫瑰.png',
    'assets/flavors-normalized/水蜜桃.png',
    'assets/flavors-normalized/荔枝.png',
    'assets/flavors-normalized/梨.png',
    'assets/flavors-normalized/柑橘.png',
    'assets/ui/wordmarks-v2/add-candidate.svg',
    'assets/ui/wordmarks-v2/analysis-result.svg',
    'assets/ui/wordmarks-v2/ask-merchant.svg',
    'assets/ui/wordmarks-v2/select-tea.svg',
    'assets/ui/wordmarks-v2/updated-judgment.svg',
    'test-fixtures/demo-images/candidate-a-qingxiang-1.png',
    'test-fixtures/demo-images/candidate-a-qingxiang-2.png',
  ];
  for (const relativePath of required) assert.equal(exists(relativePath), true, relativePath);

  const dockerfile = fs.readFileSync(path.join(root, 'Dockerfile'), 'utf8');
  const dockerignore = fs.readFileSync(path.join(root, '.dockerignore'), 'utf8');
  assert.match(dockerfile, /COPY \. \./);
  assert.doesNotMatch(dockerignore, /^assets(?:[\\/]|$)/m);

  const sourceFiles = ['app.js', 'styles.css', 'index.html', ...fs.readdirSync(path.join(root, 'frontend'))
    .filter(name => name.endsWith('.js') || name.endsWith('.css'))
    .map(name => path.join('frontend', name))];
  const literalReferences = new Set();
  for (const relativePath of sourceFiles) {
    const source = fs.readFileSync(path.join(root, relativePath), 'utf8');
    for (const match of source.matchAll(/(?:assets|test-fixtures)\/[^'"`\s)<>]+/g)) {
      const reference = match[0].replace(/[.,;]+$/, '');
      if (!reference.includes('${') && !reference.endsWith('/')) literalReferences.add(reference);
    }
  }
  for (const reference of literalReferences) assert.equal(exists(reference), true, reference);
});
