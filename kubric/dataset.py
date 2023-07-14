from pathlib import Path
import pickle
from datastructures import SE3, PointCloud, ParticleFrame
import numpy as np
import scipy.interpolate as interpolate


class KubricSequence():

    def __init__(self, data_file: Path):
        self.data = self._load_pkl(data_file)

    @property
    def intrinsics(self):
        focal_length = self.data["camera"]["focal_length"] * 2
        input_size_x = self.data["metadata"]["width"]
        input_size_y = self.data["metadata"]["height"]
        sensor_width = self.data["camera"]["sensor_width"]

        f_x = focal_length / sensor_width * (input_size_x / 2)
        f_y = f_x * input_size_x / input_size_y

        return {
            "fx": f_x,
            "fy": f_y,
            "cx": input_size_x / 2,
            "cy": input_size_y / 2,
        }

    def __len__(self):
        # We only have N - 1 frames where the pose before and after is known, so we only have N - 1 samples.
        return self.data["metadata"]['num_frames'] - 1

    def _load_pkl(self, pkl_file: Path):
        assert pkl_file.exists(), f"pkl_file {pkl_file} does not exist"
        with open(pkl_file, "rb") as f:
            data = pickle.load(f)
        return data

    def _image_space_to_depth(self,
                              image_space_positions,
                              depth_entries,
                              interpolation_type="nearest"):
        assert image_space_positions.shape[
            1] == 2, f"image_space_positions must have shape (N, 2), got {image_space_positions.shape}"
        assert depth_entries.shape[
            2] == 1, f"depth_image must have shape (H, W, 1), got {depth_entries.shape}"

        depth_entries = depth_entries[:, :, 0]
        xs = np.arange(depth_entries.shape[0])
        ys = np.arange(depth_entries.shape[1])

        depth_interpolator = interpolate.RegularGridInterpolator(
            (xs, ys), depth_entries, bounds_error=False)

        x_queries = image_space_positions[:, 0]
        y_queries = image_space_positions[:, 1]

        if interpolation_type == "nearest":
            x_queries = np.round(x_queries)
            y_queries = np.round(y_queries)

        depths = depth_interpolator((
            y_queries,
            x_queries,
        ))

        # add trailing dimension
        return depths[:, np.newaxis]

    def __getitem__(self, idx):
        rgb = self.data["rgb_video"][idx]
        depth = self.data["depth_video"][idx]

        position = self.data["camera"]["positions"][idx]
        quaternion = self.data["camera"]["quaternions"][idx]
        target_points = self.data["target_points"][:, idx]
        is_occluded = self.data["occluded"][:, idx]

        unoccluded_target_points = target_points[~is_occluded]

        # This may return NaNs if the target points are off camera, which is why we need to filter for only unoccluded particles.
        unoccluded_target_depths = self._image_space_to_depth(
            unoccluded_target_points, depth)

        assert len(unoccluded_target_depths) == len(unoccluded_target_points), \
            f"target_depths and unoccluded_particle_positions have different lengths, {len(unoccluded_target_depths)} != {len(unoccluded_particle_positions)}"
        assert np.isfinite(unoccluded_target_depths).all(), \
            f"target_depths contains NaNs, {unoccluded_target_depths}"

        pose = SE3.from_rot_w_x_y_z_translation_x_y_z(*quaternion, *position)
        pointcloud = PointCloud.from_field_of_view_depth_image(
            depth[:, :, 0], self.intrinsics)

        unoccluded_target_points_3d = PointCloud.from_field_of_view_points_and_depth(
            unoccluded_target_points, unoccluded_target_depths,
            depth.shape[:2], self.intrinsics)

        target_points_3d = np.zeros((len(target_points), 3))
        target_points_3d[~is_occluded] = unoccluded_target_points_3d.points

        particle_frame = ParticleFrame(target_points_3d, is_occluded)
        return {
            "pose": pose,
            "pointcloud": pointcloud,
            "particles": particle_frame,
            "rgb": rgb
        }


class KubricSequenceLoader():

    def __init__(self, root_dir: Path) -> None:
        self.files = sorted(root_dir.glob("*.pkl"))
        assert len(
            self.files
        ) > 0, f"root_dir {root_dir} does not contain any .pkl files"

    def __len__(self):
        return len(self.files)

    def __getitem__(self, idx) -> KubricSequence:
        return KubricSequence(self.files[idx])
