from __future__ import annotations

import re
from abc import ABC, abstractmethod

from pint import Quantity

from .utils import parse_parameter_value

RE_CIAO_PARAM = re.compile(
    r'^\\?@?(?:(?P<group>\d?):)?(?P<param>.*): (?P<type>\w)\s?(?:\[(?P<softscale>.*)\])?\s?(?:\((?P<hardscale>.*)\))?\s(?P<hardval>.*)$'
)


class CIAOParameter(ABC):
    r"""
    In the file header some parameters start with '\@' instead of simply '\'. This is an indication to the software
    that the data that follows is intended for a CIAO parameter object. After the '@', you might see a number
    followed by a colon before the label. This number is what we call a “group number” and can generally be
    ignored.

    Further, after the label and its colon, you will see a single definition character of 'V', 'C', or 'S'.
    - V means Value – a parameter that contains a double and a unit of measure, and some scaling
    definitions.
    - C means Scale – a parameter that is simply a scaled version of another.
    - S means Select – a parameter that describes some selection that has been made.
    """

    @abstractmethod
    def __init__(self, name: str, value: int | float | str | Quantity, group: int = None):
        self.name = name
        self.value = value
        self.group = group

    def __str__(self):
        return str(self.value)

    def __repr__(self):
        return f'{self.name}: {self.value}'

    @property
    def ptype(self):
        return None

    @classmethod
    def from_string(cls, ciao_string):
        match = RE_CIAO_PARAM.match(ciao_string)
        if match:
            group = int(match.group('group')) if match.group('group') else None
            name = match.group('param')
            parameter_type = match.group('type')
            soft_scale = parse_parameter_value(match.group('softscale'))
            hard_scale = parse_parameter_value(match.group('hardscale')) if match.group('hardscale') else None
            value = parse_parameter_value(match.group('hardval')) if match.group('hardval') else None

            if parameter_type == 'V':
                return ValueParameter(group=group,
                                      name=name,
                                      soft_scale=soft_scale,
                                      hard_scale=hard_scale,
                                      hard_value=value)
            elif parameter_type == 'C':
                return ScaleParameter(group=group,
                                      name=name,
                                      soft_scale=soft_scale,
                                      hard_value=value)
            elif parameter_type == 'S':
                return SelectParameter(group=group,
                                       name=name,
                                       internal_designation=soft_scale,
                                       external_designation=value)
            else:
                raise ValueError(f'Not a recognized CIAO parameter type: {parameter_type}. Allowed types: V, C, S')
        else:
            raise ValueError(f'Not a recognized CIAO parameter object: {ciao_string}')

    def __getattr__(self, name):
        """ Fetch attributes from the primary value as well, e.g. units."""
        return getattr(self.value, name)

    def __add__(self, other):
        """ Define addition behavior for CIAOParameter. """
        if isinstance(other, CIAOParameter):
            # Implement generic addition logic for CIAOParameter
            return self.value + other.value
        else:
            return self.value + other

    def __radd__(self, other):
        """ Define addition behavior for CIAOParameter. """
        if isinstance(other, CIAOParameter):
            # Implement generic addition logic for CIAOParameter
            return other.value + self.value
        else:
            return other + self.value

    def __sub__(self, other):
        """ Define subtraction behavior for CIAOParameter. """
        if isinstance(other, CIAOParameter):
            # Implement generic subtraction logic for CIAOParameter
            return self.value - other.value
        else:
            return self.value - other

    def __rsub__(self, other):
        """ Define subtraction behavior for CIAOParameter. """
        if isinstance(other, CIAOParameter):
            # Implement generic subtraction logic for CIAOParameter
            return other.value - self.value
        else:
            return other - self.value

    def __mul__(self, other):
        """ Define multiplication behavior for CIAOParameter. """
        if isinstance(other, CIAOParameter):
            # Implement generic multiplication logic for CIAOParameter
            return self.value * other.value
        else:
            return self.value * other

    def __rmul__(self, other):
        """ Define multiplication behavior for CIAOParameter. """
        return self.__mul__(other)

    def __truediv__(self, other):
        """ Define division behavior for CIAOParameter. """
        if isinstance(other, CIAOParameter):
            # Implement generic division logic for CIAOParameter
            return self.value / other.value
        else:
            return self.value / other

    def __rtruediv__(self, other):
        """ Define division behavior for CIAOParameter. """
        if isinstance(other, CIAOParameter):
            # Implement generic division logic for CIAOParameter
            return other.value / self.value
        else:
            return other / self.value

    def __pow__(self, other):
        """ Define power behavior for CIAOParameter. """
        if isinstance(other, CIAOParameter):
            # Implement generic power logic for CIAOParameter
            return self.value ** other.value
        else:
            return self.value ** other

    def __rpow__(self, other):
        """ Define power behavior for CIAOParameter. """
        if isinstance(other, CIAOParameter):
            # Implement generic power logic for CIAOParameter
            return other.value ** self.value
        else:
            return other ** self.value

    def __abs__(self):
        """ Define absolute value behavior for CIAOParameter. """
        return abs(self.value)


class ValueParameter(CIAOParameter):
    """
    The Value (identified by the letter “V”) parameters have the following format:
        [soft-scale] (hard-scale) hard-value.

    A value parameter might be missing a soft-scale or a hard-scale, but must always have a hard-value.

    The hard scale is the conversion factor we use to convert LSBs into hard values.

    The hard-value is the value you would read with a voltmeter inside of
    the NanoScope electronics or inside the head. This value is always in volts with the exception of the Drive
    Frequency (which is in Hertz) and some STM parameters (which are in Amps).

    A soft-value is what the user sees on the screen when the Units: are set to Metric.

    The soft-scale is what we use to convert a hard-value into a soft-value. Soft-scales are user defined, or are
    calibration numbers that the user divines. Soft-scales in the parameters are typically not written out —
    rather, another tag appears between the brackets, like [Sens. Zsens]. In that case, you look elsewhere in the
    parameter list for tag and use that parameter's hard-value for the soft-scale.
    """

    def __init__(self,
                 name: str,
                 hard_value: float | str | Quantity,
                 group: int = None,
                 hard_scale: float | Quantity = None,
                 soft_scale: str = None,
                 soft_scale_value: float | Quantity = None):
        super().__init__(name=name, value=hard_value, group=group)
        self.hard_value = hard_value
        self.hard_scale = hard_scale
        self.soft_scale = soft_scale

        if soft_scale_value:
            self.soft_scale_value = soft_scale_value

    @property
    def ptype(self):
        return 'V'

    @property
    def ciao_string(self):
        group_string = f'{self.group}:' if self.group else ''
        hscale_string = f' ({self.hard_scale})' if self.hard_scale else ''
        sscale_string = f' [{self.soft_scale}]' if self.soft_scale else ''

        return f'\\@{group_string}{self.name}: {self.ptype}{sscale_string}{hscale_string} {self.hard_value}'


class ScaleParameter(CIAOParameter):
    """
    The Scale parameters (identified by the letter “C”) have the following format:
        [soft-scale] hard-value.
    The hard-value is almost always a scalar value.
    The soft-scale always points to another parameter – this parameter is the target of the scaling
    action.
    Most often used for the Z magnify parm to allow user to change scaling of Z scale in Off-
    line without actually affecting the real data in the file.
    """

    def __init__(self, name: str, hard_value: float | Quantity, group: int = None, soft_scale: str = None):
        super().__init__(name=name, value=hard_value, group=group)
        self.hard_value = hard_value
        self.soft_scale = soft_scale

    @property
    def ptype(self):
        return 'C'

    @property
    def ciao_string(self):
        group_string = f'{self.group}:' if self.group else ''
        return f'\\@{group_string} {self.name}: {self.ptype} [{self.soft_scale}] {self.hard_value}'


class SelectParameter(CIAOParameter):
    """
    The Select parameters (identified by the letter “S”) have the following format:
        [Internal-designation for selection] “external-designation for selection”
    """

    def __init__(self, name: str, internal_designation: str, external_designation: str, group: int = None):
        super().__init__(name=name, value=external_designation, group=group)
        self.internal_designation = internal_designation
        self.external_designation = external_designation

    @property
    def ptype(self):
        return 'S'

    @property
    def ciao_string(self):
        group_string = f'{self.group}:' if self.group else ''
        return f'\\@{group_string}{self.name}: {self.ptype} [{self.internal_designation}] "{self.external_designation}"'
