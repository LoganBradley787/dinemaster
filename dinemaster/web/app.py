"""The web app: `create_app()` builds a FastAPI app with one route per entry in `SECTIONS`.

Routes:
    GET  /                    the Start screen (tiles)
    GET  /<slug>              a section: `pages/<slug>.build(ctx, request)` -> `templates/<slug>.html`
    POST /<slug>[/<action>]   `pages/<slug>.post(ctx, request, action, form)` -> a URL to redirect to
    GET  /kit                 every component and chart-kit mark on one page (a reference for building sections)
    GET  /static/palette.css  the colour variables, written from dinemaster/palette.py
    /static/...               metro.css, app.js, sections/<slug>.css

A section never edits this file or base.html: it owns its page module, template, optional
stylesheet and charts module, all named after its slug.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import palette, tiles
from . import fmt
from .context import CalendarCache, PageContext, load_context
from .sections import SECTIONS, TILE_LINKS, Section

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"


def _static_url(name: str) -> str:
    """A /static link that changes when the file does, so a browser never shows a stale stylesheet."""
    path = STATIC / name
    return f"/static/{name}?v={int(path.stat().st_mtime)}" if path.exists() else f"/static/{name}"


def chrome(ctx: PageContext, path: str) -> dict:
    """What the top bar says at the right: the as-of chip and how fresh the data is."""
    cfg, p = ctx.cfg, ctx.params
    parts = []
    if p.as_of is not None and p.as_of != ctx.today:
        parts.append(f"viewing {fmt.day(cfg.as_of)}")
    if p.away is not None and p.away != ctx.base_cfg.exclude_away:
        parts.append("days away left out" if p.away else "days away counted")
    if not ctx.has_data or ctx.through is None:
        fresh, old = "no data yet", False
    else:
        age = (ctx.today - ctx.through).days
        fresh, old = f"data through {fmt.day(ctx.through)}, {fmt.ago(age)}", age > 7
    return {
        "as_of": f"as of {fmt.day(cfg.as_of)}",
        "changed": ctx.view_changed,
        "changes": parts,
        "reset": path,
        "fresh": fresh,
        "old": old,
    }


def create_app(config_path: Path | None = None, today: Callable[[], date] | None = None) -> FastAPI:
    """The app for one config file (default: config.toml, or the one DINEMASTER_CONFIG names).

    `today` replaces the clock (tests pin the day with it).
    """
    app = FastAPI(title="DineMaster", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/static/palette.css", name="palette")  # before the mount, so it wins over the folder
    def palette_css() -> Response:
        return Response(palette.css_vars(), media_type="text/css")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    fmt.register(templates.env)
    templates.env.globals.update(static=_static_url, sections=SECTIONS, palette_key=palette.KEY)
    calendar_cache = CalendarCache()
    app.state.calendar_cache = calendar_cache

    def context(request: Request) -> PageContext:
        return load_context(config_path, request.query_params, today() if today else None, calendar_cache)

    def render(request: Request, ctx: PageContext, template: str, view: dict, section: Section | None = None) -> Response:
        css = f"sections/{section.slug}.css" if section else None
        page = {
            "ctx": ctx,
            "cfg": ctx.cfg,
            "section": section,
            "at_start": template == "start.html",
            "chrome": chrome(ctx, request.url.path),
            "section_css": css if css and (STATIC / css).exists() else None,
            **view,
        }
        return templates.TemplateResponse(request, template, page)

    @app.get("/", name="start")
    def start(request: Request) -> Response:
        ctx = context(request)
        view: dict = {"empty": not ctx.has_data}
        if ctx.has_data:
            links = {title: ctx.url(path) for title, path in TILE_LINKS.items()}
            view["tiles_html"] = tiles.groups_html(tiles.build_groups(ctx.tiles), links)
        return render(request, ctx, "start.html", view)

    @app.get("/kit", name="kit")
    def kit_page(request: Request) -> Response:
        from .pages import kit as page

        ctx = context(request)
        return render(request, ctx, "kit.html", page.build(ctx, request))

    def add_section(section: Section) -> None:
        module = importlib.import_module(f"{__package__}.pages.{section.slug}")

        def show(request: Request) -> Response:
            ctx = context(request)
            view = module.build(ctx, request)
            if isinstance(view, Response):
                return view
            return render(request, ctx, f"{section.slug}.html", view, section)

        async def act(request: Request, action: str = "") -> Response:
            handler = getattr(module, "post", None)
            if handler is None:
                raise HTTPException(405)
            form = dict(await request.form())

            def run():
                return handler(context(request), request, action, form)

            result = await run_in_threadpool(run)
            return result if isinstance(result, Response) else RedirectResponse(str(result), status_code=303)

        app.add_api_route(section.path, show, methods=["GET"], name=section.slug)
        app.add_api_route(section.path, act, methods=["POST"], name=f"{section.slug}_post")
        app.add_api_route(section.path + "/{action}", act, methods=["POST"], name=f"{section.slug}_action")

    for section in SECTIONS:
        add_section(section)
    return app
