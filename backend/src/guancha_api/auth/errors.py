"""Authentication errors with deliberately non-sensitive messages."""


class AuthenticationError(Exception):
    """Base class for errors raised by an authentication verifier."""


class InvalidAccessToken(AuthenticationError):
    """The supplied access token cannot authenticate a user."""


class AuthenticationServiceUnavailable(AuthenticationError):
    """The configured authentication service cannot be reached or used."""


class AuthenticationNotConfigured(AuthenticationServiceUnavailable):
    """The application has no CloudBase environment configured."""
