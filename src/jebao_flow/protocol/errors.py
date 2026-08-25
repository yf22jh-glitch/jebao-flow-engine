"""Exceptions raised by the Gizwits/Jebao LAN protocol layer."""


class ProtocolError(RuntimeError):
    pass


class ProtocolDecodeError(ProtocolError):
    pass


class IncompleteFrameError(ProtocolDecodeError):
    pass


class FrameTooLargeError(ProtocolDecodeError):
    pass


class ProtocolConnectionError(ProtocolError):
    pass


class ProtocolTimeoutError(ProtocolConnectionError):
    pass


class UnexpectedResponseError(ProtocolError):
    pass


class AuthenticationError(ProtocolError):
    pass

