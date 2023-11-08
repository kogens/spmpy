from __future__ import annotations

import re
from dataclasses import dataclass

from pint import Quantity

from .utils import parse_parameter_value

RE_CIAO_PARAM = re.compile(
    r'^\\?@?(?:(?P<group>\d?):)?(?P<param>.*): (?P<type>\w)\s?(?:\[(?P<softscale>.*)\])?\s?(?:\((?P<hardscale>.*)\))?\s(?P<hardval>.*)$')


@dataclass
class CIAOParameter:
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

    name: str  # Parameter name

    def __str__(self):
        return f'{self.name}: ' + (f'"{self.value}"' if isinstance(self.value, str) else f'{self.value}')

    @property
    def value(self):
        return None

    @property
    def group(self):
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
            elif parameter_type == 'S':
                return SelectParameter(group=group,
                                       name=name,
                                       internal_designation=soft_scale,
                                       external_designation=value)
            elif parameter_type == 'C':
                return ScaleParameter(group=group,
                                      name=name,
                                      soft_scale=soft_scale,
                                      hard_value=value)
            else:
                raise ValueError(f'Not a recognized CIAO parameter type: {parameter_type}. Allowed types: V, S, C')
        else:
            raise ValueError(f'Not a recognized CIAO parameter object: {ciao_string}')

    def __getattr__(self, name):
        """ Fetch attributes from the primary value as well, e.g. units."""
        return getattr(self.value, name)


@dataclass
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

    hard_value: float | str | Quantity

    group: int = None
    hard_scale: float | Quantity = None
    soft_scale: str = None  # Usually refers to another parameter in the file
    soft_scale_value: float | Quantity = None  # Actual value of the soft scale

    @property
    def ptype(self):
        return 'V'

    @property
    def value(self):
        return self.hard_value

    @property
    def ciao_string(self):
        group_string = f'{self.group}:' if self.group else ''
        hscale_string = f' ({self.hard_scale})' if self.hard_scale else ''
        sscale_string = f' [{self.soft_scale}]' if self.soft_scale else ''

        return f'\\@{group_string}{self.name}: {self.ptype}{sscale_string}{hscale_string} {self.hard_value}'


@dataclass
class SelectParameter(CIAOParameter):
    """
    The Select parameters (identified by the letter “S”) have the following format:
        [Internal-designation for selection] “external-designation for selection”
    """
    internal_designation: str
    external_designation: str

    group: int = None

    @property
    def ptype(self):
        return 'S'

    @property
    def value(self):
        return self.external_designation

    @property
    def ciao_string(self):
        group_string = f'{self.group}:' if self.group else ''
        return f'\\@{group_string}{self.name}: {self.ptype} [{self.internal_designation}] "{self.external_designation}"'


@dataclass
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
    soft_scale: str
    hard_value: str

    group: int = None

    @property
    def ptype(self):
        return 'C'

    @property
    def value(self):
        return self.hard_value

    @property
    def ciao_string(self):
        group_string = f'{self.group}:' if self.group else ''
        return f'\\@{group_string} {self.name}: {self.ptype} [{self.soft_scale}] {self.hard_value}'
