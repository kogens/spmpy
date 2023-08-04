import re
import struct
import warnings
from dataclasses import dataclass
from datetime import datetime
from os import PathLike
from pathlib import Path

import numpy as np
import pint

# Regex for CIAO parameters (lines starting with \@ )
CIAO_REGEX = re.compile(
    r'^\\?@(?:(?P<group>\d?):)?(?P<param>.*): (?P<type>\w)\s?(?:\[(?P<softscale>.*)\])?\s?(?:\((?P<hardscale>.*)\))?\s?(?P<hardval>.*)$')

# Define regex to identify numerical values and UnitRegistry for handling units.
NUMERICAL_REGEX = re.compile(r'([+-]?\d+\.?\d*(?:[eE][+-]\d+)?)( [\wº~/*]+)?$')
UREG = pint.UnitRegistry()
UREG.define('LSB = least_significant_bit = 1')
UREG.define('Arb = arbitrary_units = 1')
UREG.define('º = deg = degree')
UREG.default_format = '~'


class SPMFile:
    """ Representation of an entire SPM file with images and metadata """

    def __init__(self, path: str | PathLike):
        self.path = Path(path)
        self.metadata = {}
        self.images = {}
        self._flat_metadata = {}

        self.load_spm()

    def __repr__(self) -> str:
        titles = [x for x in self.images.keys()]
        return f'SPM file: "{self.path.name}", {self["Date"]}. Images: {titles}'

    def __getitem__(self, item) -> tuple[int, float, str, pint.Quantity]:
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
        self._flat_metadata = {k: v for inner_dict in self.metadata.values() for k, v in inner_dict.items()}
        self.images = images


class CIAOImage:
    """ A CIAO image with metadata"""

    def __init__(self, image_metadata: dict, full_metadata: dict, file_bytes: bytes):
        self.width = None
        self.height = None
        self.image = None
        self.px_size_x, self.px_size_y = None, None
        self.x, self.y = None, None

        self.metadata = image_metadata

        # Data offset and length refer to the bytes of the original file including metadata
        data_start = image_metadata['Data offset']
        data_length = image_metadata['Data length']

        # Calculate the number of pixels in order to decode the bytestring.
        # Note: The byte lengths don't seem to follow the bytes/pixel defined in the metadata 2ith "Bytes/pixel".
        n_rows, n_cols = image_metadata['Number of lines'], image_metadata['Samps/line']
        n_pixels = n_cols * n_rows

        # Extract relevant image data from the raw bytestring of the full file and decode the byte values
        # as signed 32-bit integers (despite documentation saying 16-bit signed int).
        # https://docs.python.org/3/library/struct.html#format-characters
        bytedata = file_bytes[data_start: data_start + data_length]
        pixel_values = struct.unpack(f'{n_pixels}i', bytedata)

        # Reorder image into a numpy array and calculate the physical value of each pixel.
        self.raw_image = np.array(pixel_values).reshape(n_rows, n_cols)
        self.get_physical_units(full_metadata)
        self.title = self.metadata['2:Image Data'].internal_designation

    def __array__(self) -> np.ndarray:
        warnings.filterwarnings("ignore", category=pint.UnitStrippedWarning)
        return self.image.__array__()

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        return getattr(self.image, '__array_ufunc__')(ufunc, method, *inputs, **kwargs)

    def __getitem__(self, key) -> str | int | float | pint.Quantity:
        return self.metadata[key]

    def __repr__(self) -> str:
        reprstr = (f'{self.metadata["Data type"]} image "{self.title}" [{self.image.units}], '
                   f'{self.image.shape} px = ({self.height.m:.1f}, {self.width.m:.1f}) {self.px_size_x.u}')
        return reprstr

    def __str__(self):
        return self.__repr__()

    def __getattr__(self, name):
        return getattr(self.image, name)

    def get_physical_units(self, full_metadata: dict):
        z_scale = self.metadata['2:Z scale']
        hard_value = z_scale.value
        soft_scale_key = z_scale.sscale
        soft_scale_value = full_metadata['Ciao scan list'][soft_scale_key].value

        # The "hard scale" is used to calculate the physical value. The hard scale given in the line must be ignored,
        # and a corrected one obtained by dividing the "Hard value" by the max range of the integer, it seems.
        # NOTE: Documentation says divide by 2^16, but 2^32 gives proper results...?
        corrected_hard_scale = hard_value / 2 ** 32
        corrected_image = self.raw_image * corrected_hard_scale * soft_scale_value
        self.image = corrected_image

        # Calculate pixel sizes in physical units
        # NOTE: This assumes "Scan Size" always represents the longest dimension of the image.
        n_rows, n_cols = self.raw_image.shape
        aspect_ratio = (max(self.raw_image.shape) / n_rows, max(self.raw_image.shape) / n_cols)
        scansize = full_metadata['Ciao scan list']['Scan Size']

        px_size_rows = scansize / (n_rows - 1) / aspect_ratio[0]
        px_size_cols = scansize / (n_cols - 1) / aspect_ratio[1]

        self.px_size_y = px_size_rows
        self.px_size_x = px_size_cols

        self.height = (n_rows - 1) * px_size_rows
        self.width = (n_cols - 1) * px_size_cols
        self.y = np.linspace(0, self.height, n_rows)
        self.x = np.linspace(0, self.width, n_cols)

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
    parameter: str
    ptype: str
    value: float | str | pint.Quantity

    group: int = None
    hscale: float = None
    sscale: str = None
    internal_designation: str = None
    external_designation: str = None

    def __init__(self, ciao_string: str):
        self.ciao_string = ciao_string
        match = CIAO_REGEX.match(ciao_string)
        if match:
            self.group = int(match.group('group')) if match.group('group') else None
            self.parameter = match.group('param')
            self.ptype = match.group('type')

            if self.ptype in ['V', 'C']:
                # "Value" or "Scale" parameter
                self.sscale = parse_parameter(match.group('softscale'))
                self.hscale = parse_parameter(match.group('hardscale')) if match.group('hardscale') else None
                self.value = parse_parameter(match.group('hardval')) if match.group('hardval') else None
            elif self.ptype == 'S':
                # "Select" parameter
                self.internal_designation = parse_parameter(match.group('softscale'))
                self.external_designation = parse_parameter(match.group('hardval'))
                self.value = parse_parameter(match.group('hardval')) if match.group('hardval') else None
        else:
            raise ValueError(f'Not a recognized CIAO parameter object: {ciao_string}')

    def __getattr__(self, name):
        return getattr(self.value, name)

    def __str__(self) -> str:
        return f'{self.value}'

    def __mul__(self, other) -> pint.Quantity:
        if isinstance(other, CIAOParameter):
            return self.value * other.value
        else:
            return self.value * other

    def __truediv__(self, other) -> pint.Quantity:
        if isinstance(other, CIAOParameter):
            return self.value / other.value
        else:
            return self.value / other

    def __add__(self, other) -> pint.Quantity:
        if isinstance(other, CIAOParameter):
            return self.value + other.value
        else:
            return self.value + other

    def __sub__(self, other) -> pint.Quantity:
        if isinstance(other, CIAOParameter):
            return self.value - other.value
        else:
            return self.value - other


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
        metadata_lines = [x.decode('latin-1').lstrip('\\').strip() for x in file_lines[start_index:end_index]]
        return metadata_lines
    else:
        raise ValueError('Beginning or end of "\\*File list" missing, cannot extract metadata')


def interpret_metadata(metadata_lines: list[str], sort=False) -> dict[str, dict[str, int, float, str, pint.Quantity]]:
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
            metadata[current_section][key] = parse_parameter(value)

    metadata['File list']['Date'] = datetime.strptime(metadata['File list']['Date'], '%I:%M:%S %p %a %b %d %Y')

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


def parse_parameter(parameter_string) -> str | int | float | pint.Quantity:
    """ Parse parameters into either int, float, string or physical quantity """
    if not parameter_string:
        return parameter_string

    value_str = parameter_string.strip(' "')
    match_numerical = NUMERICAL_REGEX.match(value_str)

    if not match_numerical:
        # Parameter is not numerical, return string
        return value_str
    elif match_numerical.group(2) is None:
        # No unit detected, value is just number
        if '.' not in value_str:
            # No decimal, convert to integer
            return int(value_str)
        else:
            # Decimal present, convert to float
            return float(value_str)
    else:
        # Value with unit
        return UREG.Quantity(value_str)
