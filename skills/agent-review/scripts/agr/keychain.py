import ctypes
import json
import subprocess

from . import ReviewError


def present(service, account):
    result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', service, '-a', account],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    if result.returncode == 44:
        return False
    if result.returncode:
        raise ReviewError('Cannot access macOS Keychain for ' + service + '; unlock the login keychain and retry')
    return True


class Item:
    def __init__(self, service, account):
        self.security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        self.foundation = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.security.SecKeychainFindGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
        self.security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
        self.security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self.foundation.CFRelease.argtypes = [ctypes.c_void_p]
        self.foundation.CFRelease.restype = None
        self.service = service.encode('utf-8')
        self.account = account.encode('utf-8')
        self.reference = ctypes.c_void_p()

    def __enter__(self):
        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        result = self.security.SecKeychainFindGenericPassword(None, len(self.service), self.service,
            len(self.account), self.account, ctypes.byref(length), ctypes.byref(data), ctypes.byref(self.reference))
        if result:
            raise ReviewError('Cannot read macOS Keychain credentials (status ' + str(result) + '); unlock the keychain or sign in again')
        try:
            self.value = json.loads(ctypes.string_at(data, length.value))
            if not isinstance(self.value, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            self.foundation.CFRelease(self.reference)
            raise ReviewError('macOS Keychain credential payload is invalid; sign in again with the selected agent') from None
        finally:
            self.security.SecKeychainItemFreeContent(None, data)
        return self

    def replace(self, value):
        payload = json.dumps(value, ensure_ascii=False).encode('utf-8')
        buffer = ctypes.create_string_buffer(payload)
        result = self.security.SecKeychainItemModifyAttributesAndData(self.reference, None, len(payload), buffer)
        if result:
            raise ReviewError('Cannot update macOS Keychain credentials (status ' + str(result) + ')')

    def __exit__(self, *args):
        self.foundation.CFRelease(self.reference)
