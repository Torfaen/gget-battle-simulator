"""Export the original battlefield textures used by the Tk board."""

import argparse
from dataclasses import dataclass
import json
from math import ceil, floor
from pathlib import Path
import re
from tempfile import NamedTemporaryFile, TemporaryDirectory

from battle_camera import BattleCamera, FAR_CLIP, NEAR_CLIP
from prepare_stage import ARTIFACTS, indexed_bundle, prepared_stage_ids, resource_index, stage_directory, stage_tables
from tools.extraction.decode_octo_koma import decode_item

import UnityPy
from PIL import Image, ImageChops, ImageDraw
from UnityPy.export.MeshExporter import MeshHandler


PIXELS_PER_CELL = 72
SPACE = (20, 24, 43)
PREVIEW_SIZE = (2880, 1620)
PREVIEW_ZOOM = 0.26
RENDER_VERSION = 2


@dataclass
class TextureLayer:
    reader: object
    scale: tuple
    offset: tuple
    opacity: float = 1.0
    additive: bool = False


@dataclass
class MapMesh:
    vertices: list
    uv: list
    triangles: list
    layers: tuple
    transparent: bool
    sky: bool


def transform_point(point, position, rotation, scale):
    x, y, z = point[0] * scale.x, point[1] * scale.y, point[2] * scale.z
    qx, qy, qz, qw = rotation.x, rotation.y, rotation.z, rotation.w
    tx, ty, tz = 2 * (qy * z - qz * y), 2 * (qz * x - qx * z), 2 * (qx * y - qy * x)
    return (x + qw * tx + qy * tz - qz * ty + position.x,
            y + qw * ty + qz * tx - qx * tz + position.y,
            z + qw * tz + qx * ty - qy * tx + position.z)


def textured_triangles(environment, *, include_sky=False):
    transform_chains = {}

    def transforms(reader):
        key = id(reader)
        if key not in transform_chains:
            transform = reader.read()
            local = transform.m_LocalPosition, transform.m_LocalRotation, transform.m_LocalScale
            parent = transform.m_Father
            transform_chains[key] = (local, *transforms(parent.deref())) if parent.path_id else (local,)
        return transform_chains[key]

    for obj in environment.objects:
        if obj.type.name != 'MeshFilter':
            continue
        mesh_filter = obj.read()
        if not mesh_filter.m_Mesh.path_id:
            continue
        game_object = mesh_filter.m_GameObject.read()
        name = game_object.m_Name.lower()
        sky = name == 'air' or any(part in name for part in ('_space', '_dome', '_sky'))
        if sky and not include_sky:
            continue
        components = {reader.type.name: reader for entry in game_object.m_Component
                      if entry.component.path_id and (reader := entry.component.deref())}
        if 'MeshRenderer' not in components or 'Transform' not in components:
            continue
        mesh = MeshHandler(mesh_filter.m_Mesh.deref().read())
        mesh.process()
        if not mesh.m_UV0:
            continue
        transform = components['Transform']
        vertices = mesh.m_Vertices
        for position, rotation, scale in transforms(transform):
            vertices = [transform_point(point, position, rotation, scale) for point in vertices]
        renderer = components['MeshRenderer'].read()
        if not renderer.m_Enabled:
            continue
        for pointer, triangles in zip(renderer.m_Materials, mesh.get_triangles()):
            if not pointer.path_id:
                continue
            material_data = pointer.deref().read()
            props = dict(material_data.m_SavedProperties.m_TexEnvs)
            cloud = props.get('_CloudTexture')
            material = next((props[key] for key in ('_BaseMap', '_MainTex', '_CloudTexture')
                             if key in props and props[key].m_Texture.path_id), None)
            if material is None:
                continue
            transparent = material is cloud or dict(material_data.stringTagMap or ()).get('RenderType') == 'Transparent'
            colors = dict(material_data.m_SavedProperties.m_Colors)
            layers = []
            for kind, layer in (('Base', material), ('Secondary', props.get('_SecondaryTex'))):
                if layer is None or not layer.m_Texture.path_id or (kind == 'Secondary' and not (sky and include_sky)):
                    continue
                scale, offset = layer.m_Scale, layer.m_Offset
                repeat = colors.get(f'_{kind}Repeat') if sky else None
                shift = colors.get(f'_{kind}Offset') if sky else None
                color = colors.get(f'_{kind}TexColor') if kind == 'Secondary' else None
                tile = 1.0
                if layer is cloud:
                    tile = dict(material_data.m_SavedProperties.m_Floats).get('_CloudTile', 1.0)
                    color = colors.get('_CloudColor')
                layers.append(TextureLayer(layer.m_Texture.deref(),
                                           (scale.x * tile * (repeat.r if repeat else 1),
                                            scale.y * tile * (repeat.g if repeat else 1)),
                                           (offset.x + (shift.r if shift else 0), offset.y + (shift.g if shift else 0)),
                                           color.a if color else 1.0, kind == 'Secondary'))
            yield MapMesh(vertices, mesh.m_UV0, list(triangles), tuple(layers), transparent, sky or material is cloud)


def draw_triangle(board, points, uv, texture):
    (x0, y0), (x1, y1), (x2, y2) = points
    left, top = max(0, ceil(min(x0, x1, x2) - .5)), max(0, ceil(min(y0, y1, y2) - .5))
    right = min(board.width, ceil(max(x0, x1, x2) - .5))
    bottom = min(board.height, ceil(max(y0, y1, y2) - .5))
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if right <= left or bottom <= top or abs(det) < 1e-5:
        return
    # The one-pixel gutter keeps samples at a UV edge inside the original texture.
    (u0, v0), (u1, v1), (u2, v2) = [(1 + u * (texture.width - 2), 1 + (1 - v) * (texture.height - 2))
                                      for u, v in uv]
    a = ((u1 - u0) * (y2 - y0) - (u2 - u0) * (y1 - y0)) / det
    b = ((u2 - u0) * (x1 - x0) - (u1 - u0) * (x2 - x0)) / det
    d = ((v1 - v0) * (y2 - y0) - (v2 - v0) * (y1 - y0)) / det
    e = ((v2 - v0) * (x1 - x0) - (v1 - v0) * (x2 - x0)) / det
    size = right - left, bottom - top
    transform = a, b, u0 + a * (left - x0) + b * (top - y0), d, e, v0 + d * (left - x0) + e * (top - y0)
    patch = texture.transform(size, Image.Transform.AFFINE, transform, Image.Resampling.BICUBIC)
    mask = Image.new('L', size)
    ImageDraw.Draw(mask).polygon([(x - left, y - top) for x, y in points], fill=255)
    if patch.mode == 'RGBA':
        mask = ImageChops.multiply(mask, patch.getchannel('A'))
    board.paste(patch, (left, top), mask)


def texture_with_edges(texture):
    w, h = texture.size
    padded = Image.new(texture.mode, (w + 2, h + 2))
    padded.paste(texture, (1, 1))
    padded.paste(texture.crop((0, 0, 1, h)), (0, 1))
    padded.paste(texture.crop((w - 1, 0, w, h)), (w + 1, 1))
    padded.paste(padded.crop((0, 1, w + 2, 2)), (0, 0))
    padded.paste(padded.crop((0, h, w + 2, h + 1)), (0, h + 1))
    return padded


def export_map(environment, mesh_parts, width, height, origin):
    size = width * PIXELS_PER_CELL, height * PIXELS_PER_CELL
    textures = [obj for obj in environment.objects if obj.type.name == 'Texture2D']
    starfield = next((obj for obj in textures if '_space_001_' in obj.peek_name()), None)
    if starfield is None:
        starfield = next((obj for obj in textures if '_space_' in obj.peek_name()), None)
    if starfield is None:
        starfield = next((obj for obj in textures if obj.peek_name().endswith('_sky_middle_large')), None)
    board = (starfield.read().image.convert('RGB').resize(size) if starfield else Image.new('RGB', size, SPACE))
    geometry = [part for part in mesh_parts if not part.sky and overlaps_board(part, width, height, origin)]
    # Unity draws transparent materials after opaque geometry, blending their texture alpha.
    for mesh in sorted(geometry, key=lambda part: (part.transparent, max(v[1] for v in part.vertices))):
        layer = mesh.layers[0]
        texture = texture_with_edges(layer.reader.read().image.convert('RGBA' if mesh.transparent else 'RGB'))
        uv = [(u * layer.scale[0] + layer.offset[0], v * layer.scale[1] + layer.offset[1]) for u, v in mesh.uv]
        for tri in mesh.triangles:
            points = [((mesh.vertices[i][0] - origin[0]) * PIXELS_PER_CELL,
                       (height - mesh.vertices[i][2] + origin[1]) * PIXELS_PER_CELL)
                      for i in tri]
            draw_triangle(board, points, [uv[i] for i in tri], texture)
    if not geometry and starfield is None:
        raise ValueError('No textured terrain in the playable map area')
    return board


def overlaps_board(mesh, width, height, origin):
    x, z = origin
    return (min(v[0] for v in mesh.vertices) < x + width and max(v[0] for v in mesh.vertices) > x and
            min(v[2] for v in mesh.vertices) < z + height and max(v[2] for v in mesh.vertices) > z)


def backdrop_height(mesh_parts, width, height, origin):
    solid = [part for part in mesh_parts if not part.sky and not part.transparent]
    if any(overlaps_board(part, width, height, origin) and max(v[1] for v in part.vertices) >= -10
           for part in solid):
        return None
    scenery = solid or [part for part in mesh_parts if not part.transparent]
    return min(-25, max((max(v[1] for v in part.vertices) for part in scenery), default=-4500))


def clip_near_plane(vertices):
    clipped = []
    previous = vertices[-1]
    for point in vertices:
        before, after = previous[2] >= NEAR_CLIP, point[2] >= NEAR_CLIP
        if before != after:
            ratio = (NEAR_CLIP - previous[2]) / (point[2] - previous[2])
            x, y, _, u, v = (a + (b - a) * ratio for a, b in zip(previous, point))
            clipped.append((x, y, NEAR_CLIP, u, v))
        if after:
            clipped.append(point)
        previous = point
    return clipped


def rasterize_mesh(board, depth_buffer, camera, mesh):
    import numpy as np

    vertices = [(*camera.view_point(*vertex), *uv) for vertex, uv in zip(mesh.vertices, mesh.uv)]
    width, height = camera.width, camera.height
    focal = camera.focal_length
    textures = []
    for layer in mesh.layers:
        texture = layer.reader.read()
        wrap = texture.m_TextureSettings
        textures.append((np.asarray(texture.image.convert('RGBA')), wrap.m_WrapU == 0, wrap.m_WrapV == 0, layer))

    def triangles_in_view():
        for triangle in mesh.triangles:
            points = [vertices[i] for i in triangle]
            if any(point[2] < NEAR_CLIP for point in points):
                points = clip_near_plane(points)
            for i in range(1, len(points) - 1):
                yield points[0], points[i], points[i + 1]

    for triangle in triangles_in_view():
        (ax, ay), (bx, by), (cx, cy) = ((width / 2 + focal * x / d, height / 2 - focal * y / d)
                                       for x, y, d, _, _ in triangle)
        left, top = max(0, floor(min(ax, bx, cx))), max(0, floor(min(ay, by, cy)))
        right, bottom = min(width, ceil(max(ax, bx, cx))), min(height, ceil(max(ay, by, cy)))
        area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if right <= left or bottom <= top or abs(area) < 1e-6:
            continue

        xs = np.arange(left, right, dtype=np.float32)[None, :] + 0.5 - ax
        ys = np.arange(top, bottom, dtype=np.float32)[:, None] + 0.5 - ay
        b = (xs * (cy - ay) - ys * (cx - ax)) / area
        c = (ys * (bx - ax) - xs * (by - ay)) / area
        a = 1 - b - c
        da, db, dc = (point[2] for point in triangle)
        inverse_depth = a / da + b / db + c / dc
        region = depth_buffer[top:bottom, left:right]
        visible = (a >= 0) & (b >= 0) & (c >= 0) & (inverse_depth >= 1 / FAR_CLIP) & (inverse_depth > region)
        if not visible.any():
            continue
        weights = (a[visible] / da, b[visible] / db, c[visible] / dc)
        perspective = inverse_depth[visible]
        u = sum(weight * point[3] for weight, point in zip(weights, triangle)) / perspective
        v = sum(weight * point[4] for weight, point in zip(weights, triangle)) / perspective

        pixels = board[top:bottom, left:right]
        color = pixels[visible].astype(np.float32)
        for image, repeat_u, repeat_v, layer in textures:
            rgba = sample_texture(image, u * layer.scale[0] + layer.offset[0],
                                  v * layer.scale[1] + layer.offset[1], repeat_u, repeat_v)
            rgb, opacity = rgba[:, :3], rgba[:, 3:4] * (layer.opacity / 255)
            if layer.additive:
                # The original sky material boosts its tiled secondary layer over the star field.
                color += rgb * opacity
            elif mesh.transparent:
                color = color * (1 - opacity) + rgb * opacity
            else:
                color = rgb
        pixels[visible] = color.clip(0, 255).astype(np.uint8)
        if not mesh.transparent:
            region[visible] = perspective


def sample_texture(image, u, v, repeat_u, repeat_v):
    import numpy as np

    height, width = image.shape[:2]
    u = (u % 1 if repeat_u else u.clip(0, 1)) * (width - 1)
    v = (1 - (v % 1 if repeat_v else v.clip(0, 1))) * (height - 1)
    x, y = u.astype(np.int32), v.astype(np.int32)
    nx, ny = np.minimum(x + 1, width - 1), np.minimum(y + 1, height - 1)
    fx, fy = (u - x)[:, None], (v - y)[:, None]
    top = image[y, x] * (1 - fx) + image[y, nx] * fx
    bottom = image[ny, x] * (1 - fx) + image[ny, nx] * fx
    return top * (1 - fy) + bottom * fy


def export_camera_view(mesh_parts, width, height, origin):
    import numpy as np

    # The backdrop is distant. Sample a wide view once so panning does not drag a baked board texture.
    view_width, view_height = PREVIEW_SIZE
    camera = BattleCamera(width / 2 + origin[0], height / 2 + origin[1], view_width, view_height, zoom=PREVIEW_ZOOM)
    if not mesh_parts:
        raise ValueError('No original scenery meshes found for the tactical camera')
    board = np.empty((view_height, view_width, 3), dtype=np.uint8)
    board[:] = SPACE
    depth = np.zeros((view_height, view_width), dtype=np.float32)
    for part in sorted(mesh_parts, key=lambda mesh: mesh.transparent):
        rasterize_mesh(board, depth, camera, part)
    return Image.fromarray(board)


def background_origin(stage_id):
    source = stage_directory(stage_id) / 'StageEntity.json'
    entity = json.loads(source.read_bytes()) if source.is_file() else {}
    maps = entity.get('_maps', ())
    origin = maps[0].get('_backgroundOriginCoord', {}) if maps else {}
    return origin.get('_x', 0), origin.get('_z', 0)


def load_map_bundle(name, stage_ids, cache, fetch):
    local = [stage_directory(stage_id) / f'{name}.unity3d' for stage_id in stage_ids]
    local.extend(directory / f'{name}.unity3d' for directory in cache)
    bundle = next((path.read_bytes() for path in local if path.is_file()), None)
    if bundle is None:
        entry = resource_index()[name]
        with TemporaryDirectory(dir=cache[0]) as scratch:
            original = indexed_bundle(entry, Path(scratch) / f'{name}.bundle', fetch)
        if original is None:
            raise ValueError(f'Original map bundle absent locally: {name}')
        bundle, _ = decode_item(original, entry)
    return UnityPy.load(bundle)


def load_sky_preview(name, cache, fetch):
    # Sky-only scenes can be made entirely of Unity particle effects, whose original
    # still image already includes the stars and debris used in the scene.
    preview = f'thum_{name}'
    if preview not in resource_index():
        return None
    try:
        environment = load_map_bundle(preview, (), cache, fetch)
    except (OSError, ValueError):
        return None
    texture = next((obj for obj in environment.objects
                    if obj.type.name == 'Texture2D' and obj.peek_name() == preview), None)
    return texture.read().image.convert('RGB') if texture else None


def atomic_map_file(target, save):
    with NamedTemporaryFile(dir=target.parent, suffix='.tmp', delete=False) as file:
        pending = Path(file.name)
    try:
        save(pending)
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch-bundles', action='store_true', help='download missing map backgrounds from the original CDN')
    parser.add_argument('--stage', type=int, action='append', help='render the given prepared stage only')
    parser.add_argument('--force', action='store_true', help='rebuild all selected maps')
    parser.add_argument('--bundle-cache', type=Path, help='additional directory holding decoded original map bundles')
    args = parser.parse_args()
    UnityPy.config.FALLBACK_UNITY_VERSION = '2022.3.61f1'
    output = ARTIFACTS / 'battle_ui_assets' / 'maps'
    output.mkdir(parents=True, exist_ok=True)
    cache = [output, output / 'bundles']
    if args.bundle_cache:
        cache.append(args.bundle_cache)
    tables = stage_tables()
    selected_stages = args.stage or prepared_stage_ids()
    stages = {row['StageId']: row['MapId'] for row in tables['m_map_stage']}
    maps = {row['Id']: row for row in tables['m_map']}
    chip_extents = {map_id: (0, 0) for map_id in {stages[stage_id] for stage_id in selected_stages}}
    for row in tables['m_map_chip_motif']:
        map_id = row['MapId']
        if map_id not in chip_extents:
            continue
        width, height = chip_extents[map_id]
        for x, z in re.findall(r'\((-?\d+),\s*(-?\d+)\)', row['CoordinateList']):
            width, height = max(width, int(x) + 1), max(height, int(z) + 1)
        chip_extents[map_id] = width, height
    groups = {}
    for stage_id in selected_stages:
        map_id = stages[stage_id]
        row = maps[map_id]
        name = row['BackgroundAsset']
        columns, rows = chip_extents[map_id]
        width, height = max(row['Width'], columns), max(row['Height'], rows)
        camera_target = output / f'{stage_id}.json'
        if not args.force and not args.stage and camera_target.is_file():
            try:
                frame = json.loads(camera_target.read_bytes())
            except (OSError, ValueError):
                frame = {}
            image = output / f"{stage_id}{'-camera' if 'camera_zoom' in frame else ''}.webp"
            if frame.get('render_version') == RENDER_VERSION and frame.get('asset') == name and image.is_file():
                continue
        if name:
            groups.setdefault(name, {}).setdefault((width, height, background_origin(stage_id)), []).append(stage_id)
    errors = []
    for name, maps in groups.items():
        try:
            stages_for_bundle = [stage_id for group in maps.values() for stage_id in group]
            environment = load_map_bundle(name, stages_for_bundle, cache, args.fetch_bundles)
            meshes = list(textured_triangles(environment, include_sky=True))
            sky_preview = load_sky_preview(name, cache, args.fetch_bundles) if (
                meshes and all(part.sky and '_space_' in part.layers[0].reader.peek_name() for part in meshes)
            ) else None
            for (width, height, origin), stages_for_size in maps.items():
                reference_height = backdrop_height(meshes, width, height, origin)
                frame = {'asset': name, 'background_origin': origin, 'render_version': RENDER_VERSION}
                if reference_height is None:
                    board = export_map(environment, meshes, width, height, origin)
                    suffix = ''
                else:
                    board = sky_preview if sky_preview is not None else export_camera_view(meshes, width, height, origin)
                    suffix = '-camera'
                    frame.update(camera_zoom=1.0 if sky_preview is not None else PREVIEW_ZOOM,
                                 reference_height=reference_height)
                    if sky_preview is not None:
                        frame['fixed_backdrop'] = True
                for stage_id in stages_for_size:
                    target = output / f'{stage_id}{suffix}.webp'
                    atomic_map_file(target, lambda pending: board.save(pending, format='WEBP', quality=88))
                    atomic_map_file(output / f'{stage_id}.json',
                                    lambda pending: pending.write_text(json.dumps(frame) + '\n', encoding='utf-8'))
                    print(f'{stage_id}: {board.width} x {board.height}', flush=True)
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f'{name}: {exc}')
    if errors:
        raise SystemExit('\n'.join(errors))


if __name__ == '__main__':
    main()
