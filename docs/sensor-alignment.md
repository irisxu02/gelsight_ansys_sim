# Sensor, camera, and marker alignment

[Documentation](README.md) · [Modeling](modeling.md) · [Dataset](dataset.md)

The examples use a **nominal GelSight Mini tracker-gel profile**. Geometry and
rendering conventions follow that profile. This is not a
calibration of a particular physical device. The presets do not include
unit-specific unloaded imagery, camera intrinsics, lens-distortion parameters,
or force/deformation calibration data.
`calibrated` remains false and every run records the assumptions in `camera.json`.

## Nominal sensor profile

| Quantity | Configured profile |
|---|---|
| Gel footprint, x × y | 25.25 × 20.75 mm |
| Gel thickness | 4 mm |
| Default bulk mesh | Uniform 36 × 30 × 8 elements; approximately 0.70 × 0.69 × 0.50 mm cells |
| Optional `--refine-contact` surface cells | 0.3 × 0.3 mm over x = ±3.6 mm, y = ±3.0 mm |
| Optional refined top element thickness | Approximately 0.178 mm |
| Coating | Mechanically homogenized into the uniform gel |
| RGB width × height | 320 × 240 px, landscape |
| Reference-plane FOV, x × y | 18.6 × 14.3 mm |
| Camera | Rectified pinhole, 24 mm standoff |
| Marker layout | 7 rows × 9 columns, 63 markers |
| Marker margins | 24 px from the image edges to outermost centers |
| Dot appearance | Antialiased disks, 6 px unloaded radius |
| Dot intensity | 55% of local RGB inside each dot |
| Dot deformation | Texture attached to the deforming material |

The layout is expressed in landscape, with the long pad axis along image
columns. The equivalent portrait representation has 9 rows and 7 columns.
The outer centers use `size - 1 - margin` to preserve symmetry about the pixel
center `(159.5, 119.5)`. The physical gel extends beyond the camera FOV.
`optics.marker_grid_rows_cols` selects the layout and takes precedence over the
legacy physical-spacing fallback. `marker_radius_px` takes precedence over
`marker_radius_m`. Radius is defined in the unloaded image; its rendered outline
changes under material strain and perspective.

The nominal dimensions and rendering parameters are reference-preset values;
they are not asserted to be measurements of every Mini cartridge.

## A pad that narrows towards its sensing face

`gel.top_width_m`, `gel.top_length_m` and `gel.taper_height_m` describe a gel
bonded to a backing wider than the face it senses with. The three are declared
together or not at all: `width_m`, `length_m` and `thickness_m` stay the
backing and the total height, and the taper is measured down from the contact
face. [`custom_gel_press`](../configs/custom_gel_press.json) is the shipped
example:

| Quantity | Tapered profile |
|---|---|
| Backing, x × y | 25.25 × 20.75 mm, as the nominal pad |
| Straight wall | 3 mm (`thickness_m` 5 mm less `taper_height_m` 2 mm) |
| Taper | 2 mm, linear between the two sections |
| Sensing face, x × y | 22 × 16 mm |
| Mesh | Uniform 36 × 30 × 10; 0.611 × 0.533 mm on the face, 0.5 mm layers |
| Marker layout | 11 rows × 17 columns, 187 markers |
| Marker margins | 10 px from the image edges to outermost centers |
| Marker pitch | 1.086 mm along x, 1.305 mm along y |
| Dot appearance | 0.5 mm diameter, declared physically |

The grid is the prism's, moved: the surface quads that carry contact, the bottom
nodes that are held, and the marker attachments are all exactly what they were,
so nothing downstream distinguishes a tapered pad from a prismatic one. The taper
is a coordinate map, so it composes with every mesh rule the gel already has -
a uniform axis, `through_thickness_bias`, the refined gel mesh, an explicit
element size - each of which keeps its own grading with a node level joined to it
where the taper begins. Without that level the elements crossing the boundary
would build a chamfer of the declared shape rather than the shape.

The dots are declared by `marker_radius_m` with `marker_radius_px` left null.
That matters on this camera: a pixel radius is one number for both axes, and the
pixels are not square (0.0581 mm along x against 0.0596 mm along y), so a dot
given in pixels renders as an ellipse. A physical radius is scaled separately in
each axis and comes out round. Every dot in this array sits inside the image, so
all 187 of them are tracked, at a 1.086 mm pitch against a 0.5 mm dot. That is
three times the marker count of the nominal 7 × 9 array on a smaller face, so
the flow field is sampled far more finely while each dot stays well separated.

The public [GelSight SDK](https://github.com/gelsightinc/gsrobotics) also uses a
320 × 240 default capture size and exposes a configurable crop. Consequently,
raw capture resolution alone does not establish a physical field of view. The
[HydroShear project](https://github.com/MMintLab/hydroshear) documents the public
Mini simulation/calibration context; its device calibration does not calibrate
another physical unit.

The black silicone coating is treated as part of the uniform gel.
No separate coating modulus, thickness, or prestress is assumed. The reference
optical surface remains z=0. See [material assumptions](materials-and-contact.md).
The default RGB uses a measured example-sensor background and optical table,
rotated clockwise into landscape. Their [provenance](../src/gelsight_ansys/data/mini/PROVENANCE.md)
is retained; those assets do not calibrate an individual device.

## Pixel scale and projection

Pixel pitch is derived from the configured field of view and image dimensions.
A pixel scale measured with another crop or resize cannot be substituted without
updating the corresponding camera configuration:

- x pitch: 18.6 / 320 = **0.058125 mm/px**.
- y pitch: 14.3 / 240 = **0.0595833 mm/px**.
- Nominal focal lengths: **fx = 412.903 px**, **fy = 402.797 px**.
- Principal point: **cx = 159.5 px**, **cy = 119.5 px**.

These unequal pitches describe the chosen rectified/resized image. They are
not an assertion about the camera sensor's native pixel aspect ratio.
For camera distance d and outward gel height z, projected offsets scale by
`d / (d + z)`. Indentation has negative z. At 1 mm indentation with d=24 mm,
the scale is 24/23, about 1.0435, even without in-plane material motion.
The camera assumes zero lens distortion; raw images must first be rectified,
or the projection model extended using measured distortion coefficients.

## Material marker rendering

A translated Gaussian spot always retains its original shape. A real printed
marker belongs to the material, so different portions can move by different
amounts. The renderer finds the material coordinate at every camera ray,
then evaluates the reference dot texture there. Triangle interpolation is
perspective-correct, so dot texture and projected marker centers remain aligned.
The ANSYS displacement supplies both normal and in-plane motion. The renderer
does not add an empirical shear/dilation field.

The physical marker plot remains in gel millimetres, with arrows enlarged 10×
and a 100 µm actual-displacement key. Color measures actual in-plane movement.
Tactile RGB has no motion amplification. `marker_flow_pixel` additionally includes
the camera's apparent dilation. The image force/deformation panels retain an
orthographic gel-plane grid and are labeled separately from optical camera rays.

## Device calibration requirements

Calibration of an individual sensor depends on the following measurements:

| Component | Required data |
|---|---|
| Sensor assembly | Sensor/cartridge identity, pad dimensions, thickness, backing bond, and lateral support |
| Reference image and markers | Unloaded image at the acquisition crop, resize, and orientation; marker centers, outlines, and variation |
| Camera | Physical pixel scale, intrinsics, and distortion fitted from known geometry |
| Optical response | Known ball presses and a marker-free background from the calibrated unit |
| Gel and interface | Force–depth, surface-displacement, and shear/twist measurements |

The nominal FOV and effective standoff depend on the image-processing
conventions. Refraction through the gel and backing is not traced. The supplied
example-sensor optical table is an appearance reference. The default gel and
friction parameters remain uncalibrated.

Analytical tests verify camera rays, apparent dilation, material-coordinate
recovery, symmetric 63-marker layout, dot stretching, and agreement between a
rendered dot centroid and its projected material attachment. CPU/CUDA agreement
is checked independently. These tests establish implementation consistency;
physical agreement requires the measurements above.
