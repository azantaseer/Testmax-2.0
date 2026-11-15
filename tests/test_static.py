"""The UI has no build step (no file hashes in names), so static files must be revalidated on every load."""
from tests.conftest import new_client


def test_static_files_are_revalidated_not_cached_blindly():
    c = new_client()
    r = c.get("/static/js/dom.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
    again = c.get("/static/js/dom.js", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304  # nothing is re-sent when the file did not change
    assert c.get("/").headers["cache-control"] == "no-cache"


def test_every_file_the_page_links_is_served_and_the_token_layer_is_first():
    import re

    c = new_client()
    html = c.get("/").text
    sheets = re.findall(r'href="(/static/css/[^"]+)"', html)
    assert sheets and sheets[0].endswith("tokens.css")  # the shared tokens load before every other stylesheet
    for path in sheets + re.findall(r'src="(/static/js/[^"]+)"', html):
        assert c.get(path).status_code == 200, path
    tokens = c.get(sheets[0]).text
    for name in ("--green:", "--row-h: 41px", "--th-h: 40px", "--topbar-h: 76px", "--sidebar-w: 256px", "--pill-teal-bg:"):
        assert name in tokens
    # every module the UI imports exists (a missing file would leave a blank page)
    for module in ("table.js", "components.js", "store.js", "dom.js", "views/shell.js", "views/requirements.js"):
        assert c.get(f"/static/js/{module}").status_code == 200, module
