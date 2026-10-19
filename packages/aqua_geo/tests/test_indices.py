import numpy as np
import pytest
import xarray as xr

from aqua_geo import indices as ix

# Typical dense-mangrove surface reflectance (0–1)
BLUE, GREEN, RED, RE1, NIR, NIR08, SWIR1 = 0.03, 0.06, 0.03, 0.08, 0.30, 0.31, 0.12


def test_ndvi_known_value() -> None:
    assert ix.ndvi(np.array(NIR), np.array(RED)) == pytest.approx((0.30 - 0.03) / 0.33)


def test_evi_in_physical_range_for_reflectance_inputs() -> None:
    value = float(ix.evi(np.array(NIR), np.array(RED), np.array(BLUE)))
    # 2.5 * 0.27 / (0.30 + 0.18 - 0.225 + 1) = 0.5378
    assert value == pytest.approx(0.5378, abs=1e-4)
    assert -1 < value < 1  # the prototype produced EVI > 1 by feeding raw DNs


def test_ndwi_is_mcfeeters_and_ndmi_is_gao() -> None:
    assert ix.ndwi(np.array(GREEN), np.array(NIR)) == pytest.approx((0.06 - 0.30) / 0.36)
    assert ix.ndmi(np.array(NIR), np.array(SWIR1)) == pytest.approx((0.30 - 0.12) / 0.42)


def test_mangrove_indices() -> None:
    assert ix.mvi(np.array(GREEN), np.array(NIR), np.array(SWIR1)) == pytest.approx(
        (0.30 - 0.06) / (0.12 - 0.06)
    )
    expected_cmri = (0.27 / 0.33) - ((0.06 - 0.30) / 0.36)
    assert ix.cmri(np.array(NIR), np.array(RED), np.array(GREEN)) == pytest.approx(expected_cmri)


def test_zero_denominator_gives_nan_not_inf() -> None:
    zero = np.array([0.0])
    assert np.isnan(ix.ndvi(zero, zero)).all()
    assert np.isnan(ix.mvi(np.array([0.1]), np.array([0.2]), np.array([0.1]))).all()


def test_add_indices_on_dataset() -> None:
    shape = (2, 2)

    def band(v: float) -> xr.DataArray:
        return xr.DataArray(np.full(shape, v, dtype="float32"), dims=("y", "x"))

    ds = xr.Dataset(
        {
            "blue": band(BLUE),
            "green": band(GREEN),
            "red": band(RED),
            "rededge1": band(RE1),
            "nir": band(NIR),
            "nir08": band(NIR08),
            "swir16": band(SWIR1),
        }
    )
    out = ix.add_indices(ds)
    for name in ("ndvi", "evi", "savi", "ndwi", "mndwi", "ndmi", "mvi", "cmri", "ndre"):
        assert out[name].dtype == np.float32
        assert out[name].shape == shape
    with pytest.raises(KeyError, match="missing bands"):
        ix.add_indices(ds.drop_vars("swir16"))
