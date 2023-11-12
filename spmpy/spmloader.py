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

    def __init__(self, spmfile: str | PathLike | bytes):
        if isinstance(spmfile, (str, Path)):
            self.path = Path(spmfile)
            bytestring = self.load_from_file(spmfile)
        elif isinstance(spmfile, bytes):
            bytestring = spmfile
        else:
            raise ValueError('SPM file must be path to spm file or raw bytestring')

        self.header: dict = self.parse_header(bytestring)
        self.images: dict = self.extract_ciao_images(self.header, bytestring)

    def __repr__(self) -> str:
        titles = [x for x in self.images.keys()]
        return f'SPM file: {self["Date"]}. Images: {titles}'

    def __getitem__(self, item) -> tuple[int, float, str, Quantity]:
        """ Fetches values from the metadata when class is called like a dict """
        return self._flat_header[item]

    @property
    def _flat_header(self):
        """ Construct a "flat metadata" for accessing with __getitem__.
        Avoid "Ciao image list" as it appears multiple times """
        non_repeating_keys = [key for key in self.header.keys() if 'Ciao image list' not in key]
        return {k: v for key in non_repeating_keys for k, v in self.header[key].items()}

    @property
    def groups(self) -> dict[int | None, dict[str]]:
        """ CIAO parameters ordered by group number """
        groups = {}
        for key, value in sorted(self._flat_header.items()):
            if isinstance(value, CIAOParameter):
                if value.group in groups.keys():
                    groups[value.group].update({key.split(':', 1)[-1]: value})
                else:
                    groups[value.group] = {}
                    groups[value.group].update({key.split(':', 1)[-1]: value})

        return groups

    @staticmethod
    def load_from_file(path):
        """ Load SPM data from a file on disk """
        with open(path, 'rb') as f:
            bytestring = f.read()

        return bytestring

    @staticmethod
    def parse_header(bytestring) -> dict:
        """ Extract metadata from the file header """
        return interpret_file_header(bytestring)

    @staticmethod
    def extract_ciao_images(header: dict, bytestring: bytes) -> dict[str, CIAOImage]:
        """ Data for CIAO images are found using the metadata from the Ciao image sections in the metadata """
        images = {}
        image_sections = header['Ciao image list']
        for i, image_metadata in enumerate(image_sections):
            image = CIAOImage(bytestring, header, image_number=i)
            key = image_metadata['2:Image Data'].internal_designation
            images[key] = image

        return images


class CIAOImage:
    """ A CIAO image with metadata """

    # TODO: Revisit how aspect ratio is used so we don't elongate images
    # TODO: Move logic for converting bytes to image into separate methods
    # TODO: Validate conversion from bytes to physical units

    def __init__(self, file_bytes: bytes, file_header: dict, image_number: int):
        self.file_header = file_header
        try:
            self.image_header = file_header['Ciao image list'][image_number]
        except IndexError as e:
            raise IndexError('CIAO image not in header, specify image number '
                             f'between 0 and {len(file_header["Ciao image list"]) - 1}') from e

        # Convert bytes into pixel values
        self._raw_image = self.raw_image_from_bytes(file_bytes)

        # Save Z scale parameter from full metadata
        zscale_soft_scale = self.fetch_soft_scale_from_full_metadata(file_header, '2:Z scale')
        self.image_header.update(zscale_soft_scale)
        self.scansize = file_header['Ciao scan list']['Scan Size']

        self.width = None
        self.height = None
        self.data = None
        self.px_size_x, self.px_size_y = None, None
        self.x, self.y = None, None

        self.calculate_physical_units()
        self.title = self.image_header['2:Image Data'].internal_designation

    def raw_image_from_bytes(self, file_bytes):
        # Data offset and length refer to the bytes of the original file including metadata
        data_start = self.image_header['Data offset']
        data_length = self.image_header['Data length']

        # Calculate the number of pixels in order to decode the bytestring.
        # Note: "Bytes/pixel" is defined in the metadata but byte lengths don't seem to follow the bytes/pixel it.
        n_rows, n_cols = self.image_header['Number of lines'], self.image_header['Samps/line']
        n_pixels = n_cols * n_rows

        # Extract relevant image data from the raw bytestring of the full file and decode the byte values
        # as signed 32-bit integers in little-endian (same as "least significant bit").
        # Note that this is despite documentation saying 16-bit signed int.
        # https://docs.python.org/3/library/struct.html#format-characters
        bytestring = file_bytes[data_start: data_start + data_length]
        pixel_values = struct.unpack(f'<{n_pixels}i', bytestring)

        # Reorder image into a numpy array and calculate the physical value of each pixel.
        # Row order is reversed in stored data, so we flip up/down.
        raw_image = np.flipud(np.array(pixel_values).reshape(n_rows, n_cols))

        return raw_image

    def decode_bytes_to_image(self):
        ...

    def fetch_soft_scale_from_full_metadata(self, full_metadata, key='2:Z scale') -> dict:
        # TODO: Make the lookup when extracting metadata from the file, store within each CIAOparameter
        #  in the soft_scale_value attribute
        soft_scale_key = self.image_header[key].soft_scale
        soft_scale_value = full_metadata['Ciao scan list'][soft_scale_key].value

        return {soft_scale_key: soft_scale_value}

    @property
    def corrected_zscale(self):
        """ Returns the z-scale correction used to translate from "pixel value" to physical units in the image"""
        z_scale = self.image_header['2:Z scale']
        hard_value = z_scale.value
        soft_scale_key = z_scale.soft_scale
        soft_scale_value = self.image_header[soft_scale_key]

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
        if key in self.image_header:
            return self.image_header[key]

        # If key is not found, try without group numbers in metadata keys
        merged_keys = {k.split(':', 1)[-1]: k for k in self.image_header.keys()}
        if key in merged_keys:
            return self.image_header[merged_keys[key]]
        else:
            raise KeyError(f'Key not found: {key}')

    def __repr__(self) -> str:
        reprstr = (f'{self.image_header["Data type"]} image "{self.title}" [{self.data.units}], '
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


def interpret_file_header(header_bytestring: bytes, encoding: str = 'latin-1') \
        -> dict[str, dict[str, int, float, str, Quantity]]:
    """ Walk through all lines in metadata and interpret sections beginning with * """
    header_lines = header_bytestring.splitlines()

    metadata = {'Ciao image list': []}
    current_section = None
    current_metadata = {}
    n_image = 0

    # Walk through each line of metadata and extract sections and parameters
    for line_bytes in header_lines:
        line = line_bytes.decode(encoding).lstrip('\\')
        if line.startswith('*'):
            # Lines starting with * indicate a new section
            if current_section == 'Ciao image list':
                # If the section is an image list, append metadata to relevant list
                metadata[current_section].append(current_metadata)
                n_image += 1
            elif current_section is not None:
                # If section is a "regular" section, add metadata entry with section as key
                metadata[current_section] = current_metadata

            if line_bytes == b'\\*File list end':
                # End of header, break out of loop
                break

            # Get current secition and initialize an empty dict to contain metadata for each section
            current_section = line.strip('*')
            current_metadata = {}

        elif line.startswith('@'):
            # Line is CIAO parameter, interpret and add to current section
            ciaoparam = CIAOParameter.from_string(line)

            # The key must include the group number as the same name can appear multiple times
            key = ciaoparam.name if not ciaoparam.group else f'{ciaoparam.group}:{ciaoparam.name}'
            current_metadata[key] = ciaoparam
        else:
            # Line is regular parameter, add to metadata of current section
            key, value = line.split(':', 1)
            current_metadata[key] = parse_parameter_value(value)

    return metadata
