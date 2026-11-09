"""Map tiles + click queries for the Argo GPR velocity product.

Reads two public copies of the same Zarr data on Cloudflare R2 (no credentials):
  maps.zarr    1 month x 1 level x 180x360 cells    -> map tiles (TilesPlugin)
  series.zarr  216 months x 30 levels x 6x6 cells   -> click -> time series / profile (WMSPlugin)
"""

import os

import xarray as xr
import xpublish
from fastapi.middleware.cors import CORSMiddleware
from obstore.store import HTTPStore
from xpublish_tiles.xpublish.tiles import TilesPlugin
from xpublish_tiles.xpublish.wms import WMSPlugin
from zarr.storage import ObjectStore

DATA = os.environ.get(
    "VELOCITY_DATA_URL",
    "https://pub-16fe89fa07db4eccb4b38b9ecbf17938.r2.dev/velocity/v0",
)


def open_public_zarr(url: str) -> xr.Dataset:
    """Open a public Zarr over HTTPS with obstore (Rust). Unlike fsspec/aiohttp it is not
    tied to one asyncio event loop, so xpublish-tiles' async loading works.
    chunks=None: no dask; each request loads exactly the chunks it needs (as xpublish-tiles' CLI does)."""
    store = ObjectStore(HTTPStore.from_url(url), read_only=True)
    ds = xr.open_zarr(store, chunks=None)
    # The CF link u -> u_std ("ancillary_variables") makes xpublish-tiles attach u_std as a
    # coordinate of u, which breaks a click query asking for u and u_std together. Drop it here.
    for name in ds.data_vars:
        ds[name].attrs.pop("ancillary_variables", None)
    return ds


maps = open_public_zarr(f"{DATA}/maps.zarr")
series = open_public_zarr(f"{DATA}/series.zarr")
maps.attrs["_xpublish_id"] = "argo-velocity-v0-maps"
series.attrs["_xpublish_id"] = "argo-velocity-v0-series"

rest = xpublish.Rest(
    {"maps": maps, "series": series},
    plugins={"tiles": TilesPlugin(), "wms": WMSPlugin()},
)
app = rest.app


@app.middleware("http")
async def cache_successful_gets(request, call_next):
    """Tiles, legends and point queries of this fixed dataset version never change:
    let browsers (and any CDN) reuse them, so a looping animation only hits the server once."""
    response = await call_next(request)
    if request.method == "GET" and response.status_code == 200 and request.url.path.startswith("/datasets/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8765)))
