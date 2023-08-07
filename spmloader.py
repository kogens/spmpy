from __future__ import annotations

import re
import struct
import warnings
from dataclasses import dataclass
from datetime import datetime
from os import PathLike
from pathlib import Path

import numpy as np
import pint
from pint import UnitRegistry, Quantity

# Regex for CIAO parameters (lines starting with \@ )
RE_CIAO_PARAM = re.compile(
    r'^\\?@?(?:(?P<group>\d?):)?(?P<param>.*): (?P<type>\w)\s?(?:\[(?P<softscale>.*)\])?\s?(?:\((?P<hardscale>.*)\))?\s(?P<hardval>.*)$')

# Define regex to identify numerical values
RE_NUMERICAL = re.compile(r'^([+-]?\d+\.?\d*(?:[eE][+-]\d+)?)( 1?[^\d:]+)?$')
RE_MULTIPLE_NUMERICAL = re.compile(r'^((?:(?<!\d)[+-]?\d+\.?\d*(?:e[+-]\d+)? ?)+)([^\d:]+)?$')

# Integer size used when decoding data from raw bytestrings
INTEGER_SIZE = 2 ** 32

# Pint UnitRegistry handles physical quantities
ureg = UnitRegistry()
ureg.define(f'least_significant_bit  = {INTEGER_SIZE} = LSB')
ureg.define('arbitrary_units = [] = Arb')
ureg.define('log_arbitrary_units = [] = log_Arb')
ureg.define('log_volt = [] = log_V')
ureg.define('log_pascal = [] = log_Pa')
ureg.define('@alias degree = º')  # The files use "Ordinal indicator": º instead of the actual degree symbol: °
ureg.default_format = '~C'


class SPMFile:
    """ Representation of an entire SPM file with images and metadata """

    def __init__(self, path: str | PathLike):
        self.path = Path(path)
        self.metadata = {}
        self.images = {}
        self._flat_metadata = {}
        self._integer_size = INTEGER_SIZE

        self.load_spm()

    def __repr__(self) -> str:
        titles = [x for x in self.images.keys()]
        return f'SPM file: "{self.path.name}", {self["Date"]}. Images: {titles}'

    def __getitem__(self, item) -> tuple[int, float, str, Quantity]:
        """ Fetches values from the metadata when class is called like a dict """
        return self._flat_metadata[item]

    def load_spm(self):
        """ Load an SPM file and extract images and metadata """
        with open(self.path, 'rb') as f:
            file_bytes = f.read()

        # Extract lines and interpret metadata and images
        metadata_lines = extract_metadata_lines(file_bytes)
        metadata = interpret_metadata(metadata_lines)
        images = extract_ciao_images(metadata, file_bytes)
        self.metadata = metadata

        # Construct a "flat metadata" for accessing with __getitem__.
        # Avoid "Ciao image list" as it appears multiple times
        non_repeating_keys = [key for key in metadata.keys() if 'Ciao image list' not in key]
        self._flat_metadata = {k: v for key in non_repeating_keys for k, v in metadata[key].items()}

        self.images = images

    @property
    def groups(self) -> dict[int | None, dict[str]]:
        """ CIAO parameters ordered by group number """
        groups = {}
        for key, value in sorted(self._flat_metadata.items()):
            if isinstance(value, CIAOParameter):
                if value.group in groups.keys():
                    groups[value.group].update({key.split(':', 1)[-1]: value})
                else:
                    groups[value.group] = {}
                    groups[value.group].update({key.split(':', 1)[-1]: value})

        return groups


class CIAOImage:
    """ A CIAO image with metadata"""

    def __init__(self, image_metadata: dict, full_metadata: dict, file_bytes: bytes):
        self.width = None
        self.height = None
        self.data = None
        self.px_size_x, self.px_size_y = None, None
        self.x, self.y = None, None

        self.metadata = image_metadata

        # Data offset and length refer to the bytes of the original file including metadata
        data_start = image_metadata['Data offset']
        data_length = image_metadata['Data length']

        # Calculate the number of pixels in order to decode the bytestring.
        # Note: "Bytes/pixel" is defined in the metadata but byte lengths don't seem to follow the bytes/pixel it.
        n_rows, n_cols = image_metadata['Number of lines'], image_metadata['Samps/line']
        n_pixels = n_cols * n_rows

        # Extract relevant image data from the raw bytestring of the full file and decode the byte values
        # as signed 32-bit integers in little-endian (same as "least significant bit").
        # Note that this is despite documentation saying 16-bit signed int.
        # https://docs.python.org/3/library/struct.html#format-characters
        bytestring = file_bytes[data_start: data_start + data_length]
        pixel_values = struct.unpack(f'<{n_pixels}i', bytestring)

        # Save Z scale parameter from full metadata
        zscale_soft_scale = self.fetch_soft_scale_from_full_metadata(full_metadata, '2:Z scale')
        self.metadata.update(zscale_soft_scale)
        self.scansize = full_metadata['Ciao scan list']['Scan Size']

        # Reorder image into a numpy array and calculate the physical value of each pixel.
        # Row order is reversed in stored data, so we flip up/down.
        self._raw_image = np.flipud(np.array(pixel_values).reshape(n_rows, n_cols))
        self.calculate_physical_units()
        self.title = self.metadata['2:Image Data'].internal_designation

    def fetch_soft_scale_from_full_metadata(self, full_metadata, key='2:Z scale') -> dict:
        soft_scale_key = self.metadata[key].sscale
        soft_scale_value = full_metadata['Ciao scan list'][soft_scale_key].value

        return {soft_scale_key: soft_scale_value}

    @property
    def corrected_zscale(self):
        """ Returns the z-scale correction used to translate from "pixel value" to physical units in the image"""
        z_scale = self.metadata['2:Z scale']
        hard_value = z_scale.value
        soft_scale_key = z_scale.sscale
        soft_scale_value = self.metadata[soft_scale_key]

        # The "hard scale" is used to calculate the physical value. The hard scale given in the line must be ignored,
        # and a corrected one obtained by dividing the "Hard value" by the max range of the integer, it seems.
        # NOTE: Documentation says divide by 2^16, but 2^32 gives proper results...?
        corrected_hard_scale = hard_value / INTEGER_SIZE

        return corrected_hard_scale * soft_scale_value

    def calculate_physical_units(self):
        """ Calculate physical scale of image values """
        self.data = self._raw_image * self.corrected_zscale

        # Calculate pixel sizes in physical units
        # NOTE: This assumes "Scan Size" always represents the longest dimension of the image.
        n_rows, n_cols = self._raw_image.shape
        aspect_ratio = (max(self._raw_image.shape) / n_rows, max(self._raw_image.shape) / n_cols)

        # Calculate pixel sizes and
        self.px_size_y = self.scansize / (n_rows - 1) / aspect_ratio[0]
        self.px_size_x = self.scansize / (n_cols - 1) / aspect_ratio[1]

        self.height = (n_rows - 1) * self.px_size_y
        self.width = (n_cols - 1) * self.px_size_x

        self.y = np.linspace(0, self.height, n_rows)
        self.x = np.linspace(0, self.width, n_cols)

    def __array__(self) -> np.ndarray:
        warnings.filterwarnings("ignore", category=pint.UnitStrippedWarning)
        return self.data.__array__()

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        return getattr(self.data, '__array_ufunc__')(ufunc, method, *inputs, **kwargs)

    def __getitem__(self, key) -> str | int | float | Quantity:
        # Return metadata by exact key
        if key in self.metadata:
            return self.metadata[key]

        # If key is not found, try without group numbers in metadata keys
        merged_keys = {k.split(':', 1)[-1]: k for k in self.metadata.keys()}
        if key in merged_keys:
            return self.metadata[merged_keys[key]]
        else:
            raise KeyError(f'Key not found: {key}')

    def __repr__(self) -> str:
        reprstr = (f'{self.metadata["Data type"]} image "{self.title}" [{self.data.units}], '
                   f'{self.data.shape} px = ({self.height.m:.1f}, {self.width.m:.1f}) {self.px_size_x.u}')
        return reprstr

    def __str__(self):
        return self.__repr__()

    def __getattr__(self, name):
        return getattr(self.data, name)

    def __add__(self, other):
        if isinstance(other, CIAOImage):
            return self.data + other.data
        else:
            return self.data + other

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, CIAOImage):
            return self.data - other.data
        else:
            return self.data - other

    def __rsub__(self, other):
        return self.__sub__(other)

    def __mul__(self, other):
        if isinstance(other, CIAOImage):
            return self.data * other.data
        else:
            return self.data * other

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        if isinstance(other, CIAOImage):
            return self.data / other.data
        else:
            return self.data / other

    def __rtruediv__(self, other):
        return self.__truediv__(other)

    @property
    def extent(self) -> list[float]:
        ext = [0, self.width.magnitude, 0, self.height.magnitude]
        return ext

    @property
    def meshgrid(self) -> np.meshgrid:
        return np.meshgrid(self.x, self.y)


@dataclass
class CIAOParameter:
    r""" CIAO parameters are lines in the SPM file starting with \@ and have several "sub" parameters """
    group: int | None
    parameter: str
    ptype: str
    value: float | str | Quantity | None

    sscale: str
    internal_designation: str

    hscale: float | None = None
    external_designation: str | None = None

    def __init__(self, parameter_string: str):
        self._raw_string = parameter_string.lstrip('\\@')
        match = RE_CIAO_PARAM.match(parameter_string)
        if match:
            self.group = int(match.group('group')) if match.group('group') else None
            self.parameter = match.group('param')
            self.ptype = match.group('type')

            # "Soft scale" and "Internal designation" seem to be interchangeable
            self.sscale = parse_parameter_value(match.group('softscale'))
            self.internal_designation = self.sscale

            self.value = parse_parameter_value(match.group('hardval')) if match.group('hardval') else None

            if self.ptype in ['V', 'C']:
                # "Value" or "Scale" parameter
                self.hscale = parse_parameter_value(match.group('hardscale')) if match.group('hardscale') else None
            elif self.ptype == 'S':
                # "Select" parameter
                self.external_designation = parse_parameter_value(match.group('hardval'))
        else:
            raise ValueError(f'Not a recognized CIAO parameter object: {parameter_string}')

    def __getattr__(self, name):
        return getattr(self.value, name)

    def __repr__(self) -> str:
        return self.ciao_string

    def __str__(self) -> str:
        return f'{self.value}'

    def __mul__(self, other) -> int | float | Quantity:
        if isinstance(other, CIAOParameter):
            return self.value * other.value
        else:
            return self.value * other

    def __truediv__(self, other) -> int | float | Quantity:
        if isinstance(other, CIAOParameter):
            return self.value / other.value
        else:
            return self.value / other

    def __add__(self, other) -> int | float | Quantity:
        if isinstance(other, CIAOParameter):
            return self.value + other.value
        else:
            return self.value + other

    def __sub__(self, other) -> int | float | Quantity:
        if isinstance(other, CIAOParameter):
            return self.value - other.value
        else:
            return self.value - other

    @property
    def ciao_string(self, backslash: bool = False):
        start = '\\@' if backslash else ''
        group_string = f'{self.group}:' if self.group else ''
        hscale_string = f' ({self.hscale})' if self.hscale else ''
        sscale_string = f' [{self.sscale}]' if self.sscale or self.ptype == 'S' else ''
        if self.ptype and self.ptype == 'S':
            value_string = f' "{self.value}"'
        elif self.ptype and self.ptype in ['V', 'C']:
            value_string = f' {self.value}'
        else:
            value_string = ''
        ciao_string = f'{start}{group_string}{self.parameter}: {self.ptype}{sscale_string}{hscale_string}{value_string}'

        return ciao_string


def extract_metadata_lines(spm_bytestring: bytes) -> list[str]:
    """ Extract the metadata section between "*File list" and "*File list end" and decode and cleanup the lines """
    # Extract lines as list of bytestrings
    file_lines = spm_bytestring.splitlines()

    start_index, end_index = None, None
    for i, line in enumerate(file_lines):
        if line.strip() == b'\\*File list':
            start_index = i
        elif line.strip() == b'\\*File list end':
            end_index = i
            break

    if start_index is not None and end_index is not None:
        # Extract the identified lines between start and end. Decode strings and strip unwanted characters.
        metadata_lines = [x.decode('latin-1').lstrip('\\') for x in file_lines[start_index:end_index]]
        return metadata_lines
    else:
        raise ValueError('Beginning or end of "\\*File list" missing, cannot extract metadata')


def interpret_metadata(metadata_lines: list[str], sort=False) -> dict[str, dict[str, int, float, str, Quantity]]:
    """ Walk through all lines in metadata and interpret sections beginning with * """
    metadata = {}
    current_section = None
    n_image = 0

    # Walk through each line of metadata and extract sections and parameters
    for line in metadata_lines:
        if line.startswith('*'):
            # Lines starting with * indicate a new section
            current_section = line.strip('*')

            # "Ciao image list" appears multiple times, so we give them a number
            if current_section == 'Ciao image list':
                current_section = f'Ciao image list {n_image}'
                n_image += 1

            # Initialize an empty dict to contain metadata for each section
            metadata[current_section] = {}

        elif line.startswith('@'):
            # Line is CIAO parameter, interpret and add to current section
            ciaoparam = CIAOParameter(line)

            # Note: The "parameter" used as key is not always unique and can appear multiple times with different
            # group number. Usually not an issue for CIAO images, however.
            key = ciaoparam.parameter if not ciaoparam.group else f'{ciaoparam.group}:{ciaoparam.parameter}'
            metadata[current_section][key] = ciaoparam
        else:
            # Line is regular parameter, add to metadata of current section
            key, value = line.split(':', 1)
            metadata[current_section][key] = parse_parameter_value(value)

    if sort:
        for key, value in metadata.items():
            metadata[key] = dict(sorted(metadata[key].items()))

    return metadata


def extract_ciao_images(metadata: dict, file_bytes: bytes) -> dict[str, CIAOImage]:
    """ Data for CIAO images are found using the metadata from the Ciao image sections in the metadata """
    images = {}
    image_sections = {k: v for k, v in metadata.items() if k.startswith('Ciao image')}
    for i, image_metadata in enumerate(image_sections.values()):
        image = CIAOImage(image_metadata, metadata, file_bytes)
        key = image_metadata['2:Image Data'].internal_designation
        images[key] = image

    return images


def parse_parameter_value(value_str: str) -> str | int | float | Quantity | datetime | list[float, Quantity] | None:
    """ Parse parameters into number, string or physical quantity """

    # Value is None, return it
    if not value_str:
        return value_str

    # Strip whitespace and check for matches
    value_str = value_str.strip()
    match_numerical = RE_NUMERICAL.match(value_str)
    if match_numerical and match_numerical.group(2):
        # Value  is a quantity with unit
        unit = match_numerical.group(2)
        # A few units appear as e.g. log(Pa), replace with log_Pa etc.
        if '(' in unit:
            unit = unit.replace('(', '_').replace(')', '')
        try:
            return ureg.Quantity(float(match_numerical.group(1)), unit)
        except pint.UndefinedUnitError:
            print(f'Unit not recognized: {match_numerical.group(2)}, parameter not converted: {value_str}')
            return value_str

    elif match_numerical:
        # No unit detected, value is just number
        if '.' not in value_str:
            # No decimal, convert to integer
            return int(value_str)
        else:
            # Decimal present, convert to float
            return float(value_str)

    # Check if value is a list of numbers with possible unit
    match_multiple_numerical = RE_MULTIPLE_NUMERICAL.match(value_str)
    if match_multiple_numerical:
        # List of values separated by space, possibly with a unit
        values = match_multiple_numerical.group(1).split()
        values = [float(x) for x in values]

        if match_multiple_numerical.group(2):
            unit = match_multiple_numerical.group(2)
            unit = unit.replace('~m', 'µm') if unit else None
            values = Quantity(values, unit)

        return values

    # Try if value is a date
    try:
        value = datetime.strptime(value_str, '%I:%M:%S %p %a %b %d %Y')
        return value
    except ValueError:
        pass

    # No other matches, strip " and return value
    return value_str.strip('"')
