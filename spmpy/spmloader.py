from __future__ import annotations

import struct
from os import PathLike
from pathlib import Path

import numpy as np
from pint import Quantity

from .ciaoparams import CIAOParameter
from .utils import parse_parameter_value

# Integer size used when decoding data from raw bytestrings
INTEGER_SIZE = 2 ** 32


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
        # warnings.filterwarnings("ignore", category=UnitStrippedWarning)
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
