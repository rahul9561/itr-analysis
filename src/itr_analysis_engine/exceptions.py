"""Exception hierarchy for the ITR analysis engine.

Document-level problems (unreadable, encrypted, unsupported) are normally
reported inside the analysis result rather than raised, so that batch runs can
continue. These exceptions are raised by the lower-level building blocks and
for caller errors.
"""


class ITREngineError(Exception):
    """Base class for all engine errors."""


class DocumentError(ITREngineError):
    """The supplied document cannot be processed."""


class InvalidDocumentError(DocumentError):
    """The file is corrupt, empty or not of the declared type."""


class EncryptedDocumentError(DocumentError):
    """The PDF is password protected."""


class UnsupportedDocumentError(DocumentError):
    """The document type, form or schema version has no tested adapter."""


class DocumentTooLargeError(DocumentError):
    """The document exceeds the configured size limit."""


class MalformedValueError(ITREngineError):
    """A monetary or date value could not be parsed."""


class OCRUnavailableError(ITREngineError):
    """OCR was requested but no local OCR backend is installed."""
