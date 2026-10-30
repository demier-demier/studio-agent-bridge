//! Analysis only: this module never changes simulation states or physics.
use numpy::{PyArray1, PyReadonlyArray2, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn points<'a>(array: &'a PyReadonlyArray2<'_, f32>) -> PyResult<&'a [f32]> {
    if array.shape()[1] != 3 || array.shape()[0] == 0 || !array.is_c_contiguous() {
        return Err(PyValueError::new_err(
            "Expected nonempty C-contiguous float32 (N, 3) points",
        ));
    }
    array
        .as_slice()
        .map_err(|_| PyValueError::new_err("Points must be contiguous"))
}

/// Float32 raster arithmetic matches the NumPy reference, without Nx index,
/// mask and gather temporaries. Only the output grid is allocated.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn surface<'py>(
    py: Python<'py>,
    array: PyReadonlyArray2<'py, f32>,
    nx: usize,
    ny: usize,
    xmin: f32,
    ymin: f32,
    resolution: f32,
    radius: f32,
) -> PyResult<Bound<'py, PyArray1<f32>>> {
    let data = points(&array)?;
    let cells = nx
        .checked_mul(ny)
        .filter(|n| *n > 0 && *n <= 16_000_000)
        .ok_or_else(|| PyValueError::new_err("Raster must contain 1..16000000 cells"))?;
    if nx == 0
        || ny == 0
        || ![xmin, ymin, resolution, radius]
            .iter()
            .all(|v| v.is_finite())
        || resolution <= 0.0
        || radius < 0.0
    {
        return Err(PyValueError::new_err("Invalid finite raster parameters"));
    }
    let mut out = vec![f32::NEG_INFINITY; cells];
    for point in data.as_chunks::<3>().0 {
        if !point.iter().all(|v| v.is_finite()) {
            return Err(PyValueError::new_err("Non-finite particle coordinate"));
        }
        let ix = ((point[0] - xmin) / resolution).floor();
        let iy = ((point[1] - ymin) / resolution).floor();
        if ix >= 0.0 && ix < nx as f32 && iy >= 0.0 && iy < ny as f32 {
            let height = point[2] + radius;
            if !height.is_finite() {
                return Err(PyValueError::new_err("Non-finite particle surface height"));
            }
            let index = ix as usize * ny + iy as usize;
            out[index] = out[index].max(height);
        }
    }
    for cell in &mut out {
        if *cell == f32::NEG_INFINITY {
            *cell = f32::NAN;
        }
    }
    Ok(PyArray1::from_vec(py, out))
}

#[pymodule]
fn scenward_cache(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(surface, m)?)?;
    Ok(())
}
