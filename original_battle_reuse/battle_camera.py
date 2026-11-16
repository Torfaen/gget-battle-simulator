"""The tactical camera geometry shared by the board and the offline map exporter."""

from dataclasses import dataclass
from math import cos, radians, sin, tan


# BaseCamera in the battle scene looks down at 70 degrees. IngameCameraManager
# opens at 29.8 and interpolates the vertical FOV from 10 to 20 over 22 to 35.
PITCH_SIN = sin(radians(70))
PITCH_COS = cos(radians(70))
DEFAULT_DISTANCE = 29.8
DEFAULT_FOV = 10 + (DEFAULT_DISTANCE - 22) * (20 - 10) / (35 - 22)
NEAR_CLIP = 0.1
FAR_CLIP = 7000


@dataclass(frozen=True)
class BattleCamera:
    center_x: float
    center_z: float
    width: int
    height: int
    zoom: float = 1.0

    @property
    def focal_length(self):
        return self.height * self.zoom / (2 * tan(radians(DEFAULT_FOV) / 2))

    @property
    def cell_size(self):
        return self.focal_length / DEFAULT_DISTANCE

    def view_point(self, x, y, z):
        z -= self.center_z
        return (x - self.center_x, PITCH_COS * y + PITCH_SIN * z,
                DEFAULT_DISTANCE - PITCH_SIN * y + PITCH_COS * z)

    def project(self, x, y, z):
        horizontal, vertical, depth = self.view_point(x, y, z)
        scale = self.focal_length / depth
        return self.width / 2 + horizontal * scale, self.height / 2 - vertical * scale

    def ground_offset(self, x, y):
        focal = self.focal_length
        vertical = (self.height / 2 - y) / focal
        z = DEFAULT_DISTANCE * vertical / (PITCH_SIN - PITCH_COS * vertical)
        return (x - self.width / 2) * (DEFAULT_DISTANCE + PITCH_COS * z) / focal, z

    def ground_at(self, x, y):
        dx, dz = self.ground_offset(x, y)
        return self.center_x + dx, self.center_z + dz

    def cell_polygon(self, x, z, insets=(0, 0, 0, 0)):
        left, top, right, bottom = insets
        return tuple(self.project(px, 0, pz) for px, pz in (
            (x + left, z + 1 - top), (x + 1 - right, z + 1 - top),
            (x + 1 - right, z + bottom), (x + left, z + bottom),
        ))

    def backdrop_offset(self, frame_x, frame_z, frame_zoom, reference_height):
        dx, dz = self.center_x - frame_x, self.center_z - frame_z
        depth = DEFAULT_DISTANCE - reference_height / PITCH_SIN
        scale = frame_zoom / (2 * tan(radians(DEFAULT_FOV) / 2) * depth)
        return dx * scale, -PITCH_SIN * dz * scale
