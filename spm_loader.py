import numpy as np
import struct
import matplotlib.pyplot as plt
from os import PathLike
from pathlib import Path


class SPMData:
    def __init__(self, path: str | PathLike):
        """ Class that represents an SPM file with images and metadata """
        self.path = Path(path)
        self.metadata = None
        self.images = None

        if self.path.is_file():
            self.load_spm(self.path)

    def load_spm(self, path: str | PathLike):
        """ Load an SPM file and extract images and metadata """
        with open(path, 'rb') as f:
            file_bytes = f.read()

        metadata_lines = extract_metadata_lines(file_bytes)
        self.metadata = interpret_metadata(metadata_lines)
        image_sections = {k: v for k, v in self.metadata.items() if k.startswith('Ciao image')}

        self.images = {}
        for i, ciao_image in enumerate(image_sections.values()):
            # Data offset and length refer to the bytes of the original file including metadata
            data_start = int(ciao_image['Data offset'])
            data_length = int(ciao_image['Data length'])

            # Note: The byte lengths don't seem to follow the bytes/pixel defined in the metadata.
            # Instead we calculate the number of pixels in order to decode the bytestring
            # bytes_per_pixel = int(ciao_image['Bytes/pixel'])
            # n_pixels = data_length // bytes_per_pixel
            samples_i = int(ciao_image['Samps/line'])
            samples_j = int(ciao_image['Number of lines'])
            n_pixels = samples_i * samples_j

            # Extract image data from the raw bytestring of the full file
            bytedata = file_bytes[data_start: data_start + data_length]

            # Decode the values, assuming "long" format
            # https://docs.python.org/3/library/struct.html#format-characters
            pixel_values = struct.unpack(f'{n_pixels}i', bytedata)

            # Reorder image into a numpy array. Note that i, j may be reversed as I cant test this
            image = np.array(pixel_values).reshape(samples_i, samples_j)

            # TODO: Scale the pixel values to physical units
            self.images[f'Ciao image {i + 1}'] = image


def extract_metadata_lines(spm_bytestring: bytes) -> list[str]:
    """ Extract the metadata section within "*File list" and "*File list end" and decode and cleanup the lines """
    file_lines = spm_bytestring.splitlines()
    start_index, end_index = find_file_list_indices(file_lines)
    metadata_lines = [x.decode('ANSI').lstrip('\\').strip() for x in file_lines[start_index:end_index]]

    return metadata_lines


def find_file_list_indices(file_lines: list[bytes]) -> tuple[int, int]:
    """ Get indices of *File list and *File list end """
    start_index = -1
    end_index = -1
    for i, line in enumerate(file_lines):
        if line.strip() == b'\\*File list':
            start_index = i + 1
        elif line.strip() == b'\\*File list end':
            end_index = i
            break
    return start_index, end_index


def interpret_metadata(metadata_lines: list[str]) -> dict[str | dict[str]]:
    """ Interpret sections beginning with "*" within the metadata """
    metadata = {}
    current_section = None
    n_image = 0

    for line in metadata_lines:
        if line.startswith('*'):
            # Lines starting with * indicate a new section
            current_section = line.strip('*')

            if current_section == 'Ciao image list':
                # "Ciao image list" appears multiple times so we give them a number
                current_section = f'Ciao image list {n_image}'
                n_image += 1

            # Initialize an empty dict for each section
            metadata[current_section] = {}

        elif current_section:
            # If line is not a section, add it as metadata in the current section
            key, value = line.split(':', 1)
            metadata[current_section][key] = value.strip()

    return metadata


if __name__ == '__main__':
    # Load data as raw bytes and lines
    datapath = '20230524_003d_Si_(2,3).spm'
    spm_data = SPMData(datapath)
    print(spm_data.metadata)

    # Plot images in SPM file
    fig, ax = plt.subplots(ncols=len(spm_data.images))
    for i, image in enumerate(spm_data.images.values()):
        ax[i].imshow(image)

    plt.show()
