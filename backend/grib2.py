"""Small GRIB2 decoder for NOAA NOMADS filter subsets.

Adapted from the user's Model Forecast project. It handles the GRIB templates
used by NOAA GFS/HRRR filter output without requiring system GRIB libraries.
"""
from __future__ import annotations

import struct

import numpy as np

try:
    from numba import njit
except ImportError:  # pragma: no cover
    def njit(*args, **kwargs):
        def _passthrough(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return _passthrough


@njit(cache=True)
def _spatial_diff_order1(result, val_idx, ival1):
    result[0] = ival1
    for i in range(1, val_idx):
        result[i] = result[i] + result[i - 1]


@njit(cache=True)
def _spatial_diff_order2(result, val_idx, ival1, ival2):
    result[0] = ival1
    result[1] = ival2
    for i in range(2, val_idx):
        result[i] = result[i] + 2 * result[i - 1] - result[i - 2]


def _signed16(raw):
    val = struct.unpack(">H", raw)[0]
    if val & 0x8000:
        return -(val & 0x7FFF)
    return val


def _signed32(raw):
    val = struct.unpack(">I", raw)[0]
    if val & 0x80000000:
        return -(val & 0x7FFFFFFF)
    return val


def _scaled_int(raw: bytes, scale_raw: int) -> float | None:
    if len(raw) != 4 or raw == b"\xff\xff\xff\xff":
        return None
    value = _signed32(raw)
    if scale_raw in (255, -1):
        return float(value)
    scale = scale_raw if scale_raw < 128 else scale_raw - 256
    return float(value) * (10.0 ** -scale)


def decode_grib2(data):
    """Decode all GRIB2 messages in a byte buffer."""
    messages = []
    pos = 0
    while pos < len(data) - 4:
        if data[pos:pos + 4] != b"GRIB":
            pos += 1
            continue
        msg_len = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
        msg = _decode_message(data[pos:pos + msg_len])
        if msg is not None:
            messages.append(msg)
        pos += msg_len
    return messages


def _decode_message(data):
    grid = None
    packing = None
    product = None
    bitmap_indices = None
    packed_data = None

    pos = 16
    while pos < len(data) - 4:
        if data[pos:pos + 4] == b"7777":
            break
        sec_len = struct.unpack(">I", data[pos:pos + 4])[0]
        if sec_len < 5:
            break
        sec_num = data[pos + 4]

        if sec_num == 3:
            grid = _parse_grid(data, pos)
        elif sec_num == 4:
            product = _parse_product(data, pos)
        elif sec_num == 5:
            packing = _parse_packing(data, pos)
        elif sec_num == 6:
            bitmap_indices = _parse_bitmap(data, pos, grid["npts"] if grid else 0)
        elif sec_num == 7:
            packed_data = data[pos + 5:pos + sec_len]

        pos += sec_len

    if grid is None or packing is None or packed_data is None:
        return None

    raw_values = _unpack_data(packed_data, packing)
    values = _apply_scaling(raw_values, packing)

    npts = grid["npts"]
    if bitmap_indices is not None:
        full = np.full(npts, np.nan)
        full[bitmap_indices[:len(values)]] = values[:len(bitmap_indices)]
        values = full

    nj = grid["Nj"]
    ni = grid["Ni"]
    if len(values) == npts:
        values_2d = values.reshape(nj, ni)
    else:
        values_2d = values.reshape(-1, ni) if ni > 0 else values.reshape(1, -1)

    if grid.get("projection") == "lambert":
        lats, lons, values_2d = _lambert_to_regular(grid, values_2d)
    else:
        lats = np.linspace(grid["la1"], grid["la2"], nj)
        lo1, lo2 = grid["lo1"], grid["lo2"]
        if lo2 < lo1:
            lo2 += 360.0
        lons = np.linspace(lo1, lo2, ni)

    product = product or {}
    return {
        "category": product.get("category", -1),
        "parameter": product.get("parameter", -1),
        "level_type": product.get("level_type"),
        "level_value": product.get("level_value"),
        "forecast_time": product.get("forecast_time"),
        "lats": lats,
        "lons": lons,
        "values": values_2d,
    }


def _parse_grid(data, pos):
    tmpl = struct.unpack(">H", data[pos + 12:pos + 14])[0]
    if tmpl == 0:
        return _parse_grid_latlon(data, pos)
    if tmpl == 30:
        return _parse_grid_lambert(data, pos)
    raise ValueError(f"Unsupported grid template 3.{tmpl}")


def _parse_grid_latlon(data, pos):
    npts = struct.unpack(">I", data[pos + 6:pos + 10])[0]
    ni = struct.unpack(">I", data[pos + 30:pos + 34])[0]
    nj = struct.unpack(">I", data[pos + 34:pos + 38])[0]
    la1 = _signed32(data[pos + 46:pos + 50]) / 1e6
    lo1 = struct.unpack(">I", data[pos + 50:pos + 54])[0] / 1e6
    la2 = _signed32(data[pos + 55:pos + 59]) / 1e6
    lo2 = struct.unpack(">I", data[pos + 59:pos + 63])[0] / 1e6
    return {
        "npts": npts,
        "Ni": ni,
        "Nj": nj,
        "la1": la1,
        "la2": la2,
        "lo1": lo1,
        "lo2": lo2,
        "projection": "latlon",
    }


def _parse_grid_lambert(data, pos):
    npts = struct.unpack(">I", data[pos + 6:pos + 10])[0]
    nx = struct.unpack(">I", data[pos + 30:pos + 34])[0]
    ny = struct.unpack(">I", data[pos + 34:pos + 38])[0]
    la1 = _signed32(data[pos + 38:pos + 42]) / 1e6
    lo1 = _signed32(data[pos + 42:pos + 46]) / 1e6
    lad = _signed32(data[pos + 47:pos + 51]) / 1e6
    lov = _signed32(data[pos + 51:pos + 55]) / 1e6
    dx = struct.unpack(">I", data[pos + 55:pos + 59])[0] / 1e3
    dy = struct.unpack(">I", data[pos + 59:pos + 63])[0] / 1e3
    scan_mode = data[pos + 64]
    latin1 = _signed32(data[pos + 65:pos + 69]) / 1e6
    latin2 = _signed32(data[pos + 69:pos + 73]) / 1e6

    if lo1 < 0:
        lo1 += 360.0
    if lov < 0:
        lov += 360.0

    return {
        "npts": npts,
        "Ni": nx,
        "Nj": ny,
        "la1": la1,
        "lo1": lo1,
        "LaD": lad,
        "LoV": lov,
        "Dx": dx,
        "Dy": dy,
        "Latin1": latin1,
        "Latin2": latin2,
        "scan_mode": scan_mode,
        "projection": "lambert",
    }


_LAMBERT_CACHE = {}


def _lambert_to_regular(grid, values_2d):
    """Regrid Lambert data onto a regular lat/lon grid."""
    cache_key = (
        grid["Ni"],
        grid["Nj"],
        grid["la1"],
        grid["lo1"],
        grid["LaD"],
        grid["LoV"],
        grid["Dx"],
        grid["Dy"],
        grid["Latin1"],
        grid["Latin2"]
    )
    
    if cache_key in _LAMBERT_CACHE:
        reg_lats, reg_lons, j0c, i0c, di, dj, valid = _LAMBERT_CACHE[cache_key]
    else:
        radius = 6371229.0
        phi1 = np.radians(grid["Latin1"])
        phi2 = np.radians(grid["Latin2"])
        lov_deg = grid["LoV"]
        lov_rad = np.radians(lov_deg)

        if abs(grid["Latin1"] - grid["Latin2"]) < 1e-10:
            n = np.sin(phi1)
        else:
            n = (
                (np.log(np.cos(phi1)) - np.log(np.cos(phi2)))
                / (
                    np.log(np.tan(np.pi / 4 + phi2 / 2))
                    - np.log(np.tan(np.pi / 4 + phi1 / 2))
                )
            )

        f = np.cos(phi1) * np.tan(np.pi / 4 + phi1 / 2) ** n / n
        rho0 = radius * f / np.tan(np.pi / 4 + np.radians(grid["LaD"]) / 2) ** n

        la1_rad = np.radians(grid["la1"])
        lo1_rad = np.radians(grid["lo1"])
        rho_1 = radius * f / np.tan(np.pi / 4 + la1_rad / 2) ** n
        theta_1 = n * (lo1_rad - lov_rad)
        x0 = rho_1 * np.sin(theta_1)
        y0 = rho0 - rho_1 * np.cos(theta_1)

        nx, ny = grid["Ni"], grid["Nj"]
        dx, dy = grid["Dx"], grid["Dy"]

        x_arr = x0 + np.arange(nx) * dx
        y_arr = y0 + np.arange(ny) * dy
        xx, yy = np.meshgrid(x_arr, y_arr)

        rho = np.sign(n) * np.sqrt(xx**2 + (rho0 - yy) ** 2)
        theta = np.arctan2(xx * np.sign(n), (rho0 - yy) * np.sign(n))
        lats_2d = np.degrees(2 * np.arctan((radius * f / rho) ** (1 / n)) - np.pi / 2)
        lons_2d = np.degrees(theta / n) + lov_deg
        lons_2d = ((lons_2d + 180) % 360) - 180

        res = max(0.05, min(0.25, dx / 111_000.0))
        lat_min, lat_max = float(np.nanmin(lats_2d)), float(np.nanmax(lats_2d))
        lon_min, lon_max = float(np.nanmin(lons_2d)), float(np.nanmax(lons_2d))
        reg_lats = np.arange(lat_min, lat_max + res / 2, res)
        reg_lons = np.arange(lon_min, lon_max + res / 2, res)

        reg_lon_2d, reg_lat_2d = np.meshgrid(reg_lons, reg_lats)
        lat_rad = np.radians(reg_lat_2d)
        lon_360 = np.where(reg_lon_2d < 0, reg_lon_2d + 360, reg_lon_2d)
        lon_rad = np.radians(lon_360)

        rho_r = radius * f / np.tan(np.pi / 4 + lat_rad / 2) ** n
        theta_r = n * (lon_rad - lov_rad)
        x_r = rho_r * np.sin(theta_r)
        y_r = rho0 - rho_r * np.cos(theta_r)

        fi = (x_r - x0) / dx
        fj = (y_r - y0) / dy

        i0 = np.floor(fi).astype(int)
        j0 = np.floor(fj).astype(int)
        di = fi - i0
        dj = fj - j0
        valid = (i0 >= 0) & (i0 < nx - 1) & (j0 >= 0) & (j0 < ny - 1)
        i0c = np.clip(i0, 0, nx - 2)
        j0c = np.clip(j0, 0, ny - 2)
        
        _LAMBERT_CACHE[cache_key] = (reg_lats, reg_lons, j0c, i0c, di, dj, valid)

    result = (
        values_2d[j0c, i0c] * (1 - di) * (1 - dj)
        + values_2d[j0c, i0c + 1] * di * (1 - dj)
        + values_2d[j0c + 1, i0c] * (1 - di) * dj
        + values_2d[j0c + 1, i0c + 1] * di * dj
    )
    result[~valid] = np.nan
    return reg_lats, reg_lons, result



def _parse_product(data, pos):
    """Section 4 product metadata for common instantaneous templates."""
    product = {
        "category": data[pos + 9],
        "parameter": data[pos + 10],
    }
    sec_len = struct.unpack(">I", data[pos:pos + 4])[0]
    tmpl = struct.unpack(">H", data[pos + 7:pos + 9])[0]
    if tmpl in (0, 1, 2, 8, 11) and sec_len >= 34:
        product["forecast_time"] = struct.unpack(">I", data[pos + 18:pos + 22])[0]
        product["level_type"] = data[pos + 22]
        product["level_value"] = _scaled_int(data[pos + 24:pos + 28], data[pos + 23])
        product["level_type_2"] = data[pos + 28]
        product["level_value_2"] = _scaled_int(data[pos + 30:pos + 34], data[pos + 29])
    return product


def _parse_packing(data, pos):
    nvals = struct.unpack(">I", data[pos + 5:pos + 9])[0]
    tmpl = struct.unpack(">H", data[pos + 9:pos + 11])[0]
    ref = struct.unpack(">f", data[pos + 11:pos + 15])[0]
    binary_scale = _signed16(data[pos + 15:pos + 17])
    decimal_scale = _signed16(data[pos + 17:pos + 19])
    nbits = data[pos + 19]
    info = {
        "nvals": nvals,
        "template": tmpl,
        "ref": ref,
        "E": binary_scale,
        "D": decimal_scale,
        "nbits": nbits,
    }
    if tmpl in (2, 3):
        info["group_split_method"] = data[pos + 21]
        info["missing_mgmt"] = data[pos + 22]
        info["primary_missing"] = struct.unpack(">f", data[pos + 23:pos + 27])[0]
        info["secondary_missing"] = struct.unpack(">f", data[pos + 27:pos + 31])[0]
        info["NG"] = struct.unpack(">I", data[pos + 31:pos + 35])[0]
        info["ref_group_widths"] = data[pos + 35]
        info["nbits_group_widths"] = data[pos + 36]
        info["ref_group_lengths"] = struct.unpack(">I", data[pos + 37:pos + 41])[0]
        info["length_increment"] = data[pos + 41]
        info["last_group_length"] = struct.unpack(">I", data[pos + 42:pos + 46])[0]
        info["nbits_group_lengths"] = data[pos + 46]
        if tmpl == 3:
            info["spatial_order"] = data[pos + 47]
            info["extra_octets"] = data[pos + 48]
    if tmpl == 42:
        info["ccsds_orig_type"] = data[pos + 20]
        info["ccsds_options_mask"] = data[pos + 21]
        info["ccsds_block_size"] = data[pos + 22]
        info["ccsds_rsi"] = struct.unpack(">H", data[pos + 23:pos + 25])[0]
    return info


def _parse_bitmap(data, pos, npts):
    indicator = data[pos + 5]
    if indicator == 255:
        return None
    if indicator == 0 and npts > 0:
        nbytes = (npts + 7) // 8
        bitmap_bytes = data[pos + 6:pos + 6 + nbytes]
        bits = np.unpackbits(np.frombuffer(bitmap_bytes, dtype=np.uint8))[:npts]
        return np.where(bits == 1)[0]
    return None


def _unpack_data(packed_data, packing):
    nbits = packing["nbits"]
    nvals = packing["nvals"]
    if nbits == 0:
        return np.zeros(nvals, dtype=np.float64)

    tmpl = packing["template"]
    if tmpl == 0:
        return _unpack_simple(packed_data, nvals, nbits)
    if tmpl in (2, 3):
        return _unpack_complex(packed_data, packing)
    if tmpl == 40:
        return _unpack_jpeg2000(packed_data, nvals)
    if tmpl == 42:
        return _unpack_ccsds(packed_data, nvals, nbits, packing)
    raise ValueError(f"Unsupported packing template 5.{tmpl}")


def _unpack_simple(packed_data, nvals, nbits):
    all_bits = np.unpackbits(np.frombuffer(packed_data, dtype=np.uint8))
    total = nvals * nbits
    if total > len(all_bits):
        all_bits = np.pad(all_bits, (0, total - len(all_bits)))
    bits = all_bits[:total].reshape(nvals, nbits)
    powers = np.int64(1) << np.arange(nbits - 1, -1, -1, dtype=np.int64)
    return (bits.astype(np.int64) * powers).sum(axis=1)


def _unpack_jpeg2000(packed_data, nvals):
    import io
    from PIL import Image

    img = Image.open(io.BytesIO(packed_data))
    arr = np.array(img).flatten().astype(np.int64)
    return arr[:nvals]


def _unpack_ccsds(packed_data, nvals, nbits, packing):
    from imagecodecs import aec_decode

    block_size = packing.get("ccsds_block_size", 32)
    rsi = packing.get("ccsds_rsi", 128)
    flags = packing.get("ccsds_options_mask", 14)
    decoded = aec_decode(
        packed_data,
        bitspersample=nbits,
        blocksize=block_size,
        rsi=rsi,
        flags=flags,
    )
    if nbits <= 8:
        dtype = ">u1"
    elif nbits <= 16:
        dtype = ">u2"
    else:
        dtype = ">u4"
    arr = np.frombuffer(decoded, dtype=dtype)
    return arr[:nvals].astype(np.int64)


@njit(cache=True)
def _from_bytes_big(data, offset, length):
    val = 0
    for i in range(length):
        val = (val << 8) | int(data[offset + i])
    return val


@njit(cache=True)
def _extract_n_bits_from_bytes(data, bit_offset, n_values, n_bits):
    result = np.zeros(n_values, dtype=np.int64)
    if n_bits == 0 or n_values == 0:
        return result, bit_offset
    
    idx = bit_offset
    n_bytes = len(data)
    for i in range(n_values):
        val = 0
        for j in range(n_bits):
            byte_idx = idx >> 3
            bit_idx = 7 - (idx & 7)
            if byte_idx < n_bytes:
                bit = (int(data[byte_idx]) >> bit_idx) & 1
                val = (val << 1) | bit
            else:
                val = val << 1
            idx += 1
        result[i] = val
    return result, idx


@njit(cache=True)
def _unpack_complex_numba(
    data,
    nvals,
    ng,
    nbits_ref,
    ref_widths,
    nbits_widths,
    ref_lengths,
    length_incr,
    last_group_len,
    nbits_lengths,
    spatial_order,
    extra_octets
):
    offset = 0
    ival1 = 0
    ival2 = 0
    overall_min = 0
    if spatial_order > 0 and extra_octets > 0:
        if spatial_order >= 1:
            ival1 = _from_bytes_big(data, offset, extra_octets)
            offset += extra_octets
        if spatial_order >= 2:
            ival2 = _from_bytes_big(data, offset, extra_octets)
            offset += extra_octets
        
        raw_min = _from_bytes_big(data, offset, extra_octets)
        offset += extra_octets
        
        sign_bit = 1 << (extra_octets * 8 - 1)
        if raw_min & sign_bit:
            overall_min = -(raw_min & (sign_bit - 1))
        else:
            overall_min = raw_min

    bit_pos = 0
    remaining_data = data[offset:]

    group_refs, bit_pos = _extract_n_bits_from_bytes(remaining_data, bit_pos, ng, nbits_ref)
    bit_pos = ((bit_pos + 7) // 8) * 8

    group_widths, bit_pos = _extract_n_bits_from_bytes(remaining_data, bit_pos, ng, nbits_widths)
    bit_pos = ((bit_pos + 7) // 8) * 8
    group_widths = group_widths + ref_widths

    group_lengths, bit_pos = _extract_n_bits_from_bytes(remaining_data, bit_pos, ng, nbits_lengths)
    bit_pos = ((bit_pos + 7) // 8) * 8
    group_lengths = group_lengths * length_incr + ref_lengths
    group_lengths[-1] = last_group_len

    result = np.zeros(nvals, dtype=np.int64)
    val_idx = 0
    for g in range(ng):
        gw = int(group_widths[g])
        gl = int(group_lengths[g])
        if gl == 0:
            continue
        if gw == 0:
            result[val_idx:val_idx + gl] = group_refs[g]
        else:
            vals, bit_pos = _extract_n_bits_from_bytes(remaining_data, bit_pos, gl, gw)
            result[val_idx:val_idx + gl] = vals + group_refs[g]
        val_idx += gl

    result[:val_idx] += overall_min
    if spatial_order == 1 and val_idx > 0:
        _spatial_diff_order1(result, val_idx, ival1)
    elif spatial_order == 2 and val_idx > 1:
        _spatial_diff_order2(result, val_idx, ival1, ival2)

    return result[:nvals]


def _unpack_complex(packed_data, packing):
    nvals = packing["nvals"]
    ng = packing["NG"]
    nbits_ref = packing["nbits"]
    ref_widths = packing["ref_group_widths"]
    nbits_widths = packing["nbits_group_widths"]
    ref_lengths = packing["ref_group_lengths"]
    length_incr = packing["length_increment"]
    last_group_len = packing["last_group_length"]
    nbits_lengths = packing["nbits_group_lengths"]
    spatial_order = packing.get("spatial_order", 0)
    extra_octets = packing.get("extra_octets", 0)

    data_arr = np.frombuffer(packed_data, dtype=np.uint8)
    return _unpack_complex_numba(
        data_arr,
        nvals,
        ng,
        nbits_ref,
        ref_widths,
        nbits_widths,
        ref_lengths,
        length_incr,
        last_group_len,
        nbits_lengths,
        spatial_order,
        extra_octets
    )



def _apply_scaling(raw_values, packing):
    return (
        packing["ref"]
        + raw_values.astype(np.float64) * (2.0 ** packing["E"])
    ) * (10.0 ** (-packing["D"]))
