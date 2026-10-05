"""JSON contracts and exact Premiere sequence-time arithmetic.

Hash callers must remove their own hash field explicitly. No fields are silently
excluded here; frame conversions never accumulate approximate frame durations.
"""
import hashlib
import json
import re
import rfc8785
from fractions import Fraction

TICKS_PER_SECOND = 254016000000
SCHEMA_VERSION = 1
APP_VERSION = '0.1.0'


class CutError(Exception):
    """A blocking domain error which can be serialized at the API boundary."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def canonical_hash(value):
    def check_keys(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise TypeError('JSON object keys must be strings')
            for child in item.values():
                check_keys(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                check_keys(child)

    try:
        check_keys(value)
        # UXP uses ECMAScript number serialization and UTF-16 key ordering.
        # RFC 8785 keeps hashes identical for 1.0, small exponents and Unicode.
        encoded = rfc8785.dumps(value)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise CutError('INVALID_CONTRACT', 'Value is not canonical JSON',
                       {'cause': str(error)}) from error
    return hashlib.sha256(encoded).hexdigest()


def integer(value, field='frame'):
    if isinstance(value, bool) or not isinstance(value, int):
        raise CutError('INVALID_CONTRACT', 'Expected an integer', {'field': field})
    return value


def fps_fraction(fps):
    if not isinstance(fps, dict):
        raise CutError('INVALID_CONTRACT', 'FPS requires rational num and den')
    num = integer(fps.get('num'), 'fps.num')
    den = integer(fps.get('den'), 'fps.den')
    if num <= 0 or den <= 0:
        raise CutError('INVALID_CONTRACT', 'FPS must be positive')
    return Fraction(num, den)


def tick_int(ticks):
    if not isinstance(ticks, str) or not re.fullmatch(r'-?[0-9]+', ticks):
        raise CutError('INVALID_CONTRACT', 'Ticks must be decimal integer strings')
    return int(ticks)


def frame_ticks(frame, fps):
    value = Fraction(integer(frame) * TICKS_PER_SECOND, 1) / fps_fraction(fps)
    if value.denominator != 1:
        raise CutError('TIME_MAPPING_UNSUPPORTED', 'Frame is not an exact Premiere tick',
                       {'frame': frame, 'fps': fps})
    return str(value.numerator)


def ticks_frame(ticks, fps):
    value = Fraction(tick_int(ticks), TICKS_PER_SECOND) * fps_fraction(fps)
    # Floor(x + 1/2), including negative coordinates: a tie always goes later.
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def seconds_frames(seconds, fps):
    try:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float, str)):
            raise ValueError('Expected decimal seconds')
        value = Fraction(str(seconds)) * fps_fraction(fps)
    except (ValueError, ZeroDivisionError) as error:
        raise CutError('INVALID_POLICY', 'Invalid duration', {'seconds': str(seconds)}) from error
    if value < 0:
        raise CutError('INVALID_POLICY', 'Duration must not be negative')
    return -(-value.numerator // value.denominator)
