# Object-oriented SPM files
An simple tool for working with Scanning Probe Microscopy (SPM) files, specifically for Bruker AFM files.

## Setup
Requires Python >= 3.9 with `numpy` and [`pint`](https://pint.readthedocs.io/) for units, 
optionally `matplotlib` for plotting examples below
```
pip install numpy pint matplotlib
```

Install the module from the GitHub url
```
pip install git+https://github.com/kogens/spmpy
```


## Loading SPM files
An .spm file is represented by the `SPMFile` class and can be loaded by passing the path directly to `SPMFile`:

```python
>>> from spmpy import SPMFile
>>> spm_file = SPMFile('path/to/afm_testfile.spm')
>>> print(spm_file)
SPM file: afm_testfile.spm, 2023-05-24 10:55:19. Images: ['ZSensor', 'AmplitudeError', 'Phase']
```


All the metadata is accessible in `spm_file.header` where it is organized in the sections starting with `*` in the 
file. You can also access the parameter directly by treating `SPMFile` as a dict, e.g. 
```python
>>> scan_size = spm_file['Scan Size']
>>> z_sensor_scaling = spm_file['Sens. ZsensSens']
>>> print(scan_size)
4500.0 nm
>>> print(z_sensor_scaling)
Sens. ZsensSens: 872.0382 nm/V
```

Each parameter in the metadata is interpreted into relevant datatypes (numbers, strings etc), 
and parameters with units are represented as a `Quantity` from the [Pint](https://pint.readthedocs.io/) library.


### Images in the SPM file
An SPM file usually has more than one image and can be accessed with `spm_file.images`:
```
>>> spm_file.images
{'ZSensor': AFM image "Height Sensor" [nm], (128, 128) px = (4.5, 4.5) micrometer,
 'AmplitudeError': AFM image "Amplitude Error" [mV*nm/V], (128, 128) px = (4.5, 4.5) micrometer,
 'Phase': AFM image "Phase" [deg], (128, 128) px = (4.5, 4.5) micrometer}
```

These images have already been converted to physical units and  can be plotted directly:
```python
import matplotlib.pyplot as plt

height_im = spm_file.images['ZSensor']
plt.imshow(height_im)
plt.show()
```

This will show the image with pixels on x and y-axis. For `imshow()` you can set an `extent` to show the physical units.
Either calculate the extent with `image.px_size_x` and `image.px_size_y` or use the built in `image.extent` value.
The coordinates are also available as `image.x`, `image.y` or meshgrids in `image.meshgrid`.
```python
# Plot the height image with units
im = plt.imshow(height_im, extent=height_im.extent)
plt.title(height_im.title)
plt.xlabel(height_im.x.units)
plt.ylabel(height_im.y.units)
cbar = plt.colorbar(im)
cbar.set_label(f'{height_im["Image Data"]} [{height_im.units}]')
plt.show()
```

The underlying data is stored as Pint `Quantity` objects which handles the units and support most of the same operations
as a numpy `ndarray`, such as addition, multiplication etc. with other images although this will currently strip the 
metadata
```python
min_value, max_value = height_im.min(), height_im.max()

# Set zero point at the lowest value
height_im_zeroed = height_im - min_value

# Normalize image to have values from 0 to 1
height_im_normalized = (height_im-min_value)/(max_value-min_value)
```

For some operations you need the pure Numpy `ndarray`, e.g. for many [scikit-image](https://scikit-image.org/) functions, and 
this can be accessed using the `.magnitude` or `.m` attribute:
```python
# Get underlying numpy ndarray
raw_numpy_array = height_im.m
print(type(raw_numpy_array))
```


All images found in the SPM file, such as phase and amplitude error images
can be plotted like so
```python
# Plot images in SPM file
fig, ax = plt.subplots(ncols=len(spm_file.images))
for i, image in enumerate(spm_file.images.values()):
    im = ax[i].imshow(image, extent=image.extent)
    ax[i].set_title(image.title)
    ax[i].set_xlabel(image.x.units)
    ax[i].set_ylabel(image.y.units)

plt.tight_layout()
plt.show()
```
