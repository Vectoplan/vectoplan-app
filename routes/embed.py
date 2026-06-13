# /services/app/routes/embed.py
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse, unquote, urlunparse, urljoin

import requests
from flask import Blueprint, request, Response, abort, current_app, jsonify

bp = Blueprint("embed", __name__)

ALLOWED_HOSTS = {
    # VectoPlan
    "vectoplan.com", "www.vectoplan.com", "analytics.vectoplan.com",
    # Statische CDNs
    "cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com",
    # Fonts / TagManager
    "www.googletagmanager.com", "fonts.googleapis.com", "fonts.gstatic.com",
}

SESSION = requests.Session()
TIMEOUT = 25


# ───────────────────────── helpers ─────────────────────────

def _is_allowed_host(host: str) -> bool:
    return (host or "").lower() in ALLOWED_HOSTS

def _is_allowed_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and _is_allowed_host(p.hostname or "")
    except Exception:
        return False

def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"

def _abs_url(base: str, val: str) -> str:
    if val.startswith(("http://", "https://")): return val
    if val.startswith("//"):  return "https:" + val
    if val.startswith("/"):   return _origin(base) + val
    return urljoin(base, val)

def _skip_proxy(val: str) -> bool:
    v = (val or "").strip().lower()
    return (not v) or v.startswith((
        "/embed/", "/_nuxt/", "#", "data:", "javascript:", "mailto:", "tel:", "about:blank"
    ))

def _asset_proxy(url: str) -> str: return f"/embed/a?u={url}"

def _std_headers(ct: str) -> dict:
    return {
        "X-Frame-Options": "ALLOWALL",
        "Content-Security-Policy": "frame-ancestors *",
        "Cache-Control": "no-store",
        "Content-Type": ct,
    }

def _auth_header_for(url_or_host: str) -> dict:
    """Bei VectoPlan-Host den PAT mitschicken."""
    try:
        host = url_or_host
        if "://" in host:
            host = (urlparse(url_or_host).hostname or "").lower()
        else:
            host = (host or "").lower()
        if host.endswith("vectoplan.com"):
            tok = (current_app.config.get("VECTOPLAN_TOKEN") or "").strip()
            if tok:
                return {"Authorization": f"Bearer {tok}"}
    except Exception:
        pass
    return {}

def _inject_css_and_hooks(html: str) -> str:
    """Minimal-CSS + fetch/XMLHttpRequest Hook: VectoPlan-Requests gehen über unsere Proxy-Routen (inkl. PAT)."""
    strip = r"""
<style id="vp-embed">
  html,body{height:100%!important;width:100%!important;margin:0!important;padding:0!important;overflow:hidden!important}
  header,nav,footer,.cookie,[class*="cookie"]{display:none!important}
  aside[aria-label="Informationen"]{display:none!important}
</style>
<script>(function(){
  function isVp(u){try{var a=new URL(u,location.href);return /(^|\.)vectoplan\.com$/i.test(a.hostname);}catch(e){return false;}}
  function pGet(u){return "/embed/a?u="+encodeURIComponent(new URL(u,location.href).href);}
  function pAny(u){return "/embed/x?u="+encodeURIComponent(new URL(u,location.href).href);}
  var OF=window.fetch;
  window.fetch=function(input,init){
    try{
      var u=(typeof input==="string")?input:(input&&input.url||"");
      if(isVp(u)){
        var m=(init&&init.method)||"GET";
        var tgt=(String(m).toUpperCase()==="GET")?pGet(u):pAny(u);
        return OF(tgt,Object.assign({},init||{}, {credentials:"omit"}));
      }
    }catch(e){}
    return OF(input,init);
  };
  if(window.XMLHttpRequest){
    var OX=XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open=function(method,url){
      try{
        if(isVp(url)){
          var tgt=(String(method).toUpperCase()==="GET")?pGet(url):pAny(url);
          return OX.apply(this,[method,tgt].concat([].slice.call(arguments,2)));
        }
      }catch(e){}
      return OX.apply(this,arguments);
    };
  }
  var hide=function(){document.querySelectorAll('aside[aria-label=\"Informationen\"]').forEach(function(e){e.style.display=\"none\";});};
  new MutationObserver(hide).observe(document.documentElement,{subtree:true,childList:true});
  hide();
})();</script>
"""
    try:
        return re.sub(r"(?i)</head>", strip + "</head>", html, count=1) if re.search(r"(?i)</head>", html) else strip + html
    except Exception:
        return strip + html

def _rewrite_html(html: str, target_url: str) -> str:
    base = target_url
    out = html

    def repl_dq(m: re.Match) -> str:
        val = m.group(2)
        if _skip_proxy(val): return m.group(0)
        absu = _abs_url(base, val)
        if not _is_allowed_url(absu): return m.group(0)
        return f'{m.group(1)}"{_asset_proxy(absu)}"'

    def repl_sq(m: re.Match) -> str:
        val = m.group(2)
        if _skip_proxy(val): return m.group(0)
        absu = _abs_url(base, val)
        if not _is_allowed_url(absu): return m.group(0)
        return f"{m.group(1)}'{_asset_proxy(absu)}'"

    out = re.sub(r'(?i)(\s(?:href|src)\s*=\s*)"(.*?)"', repl_dq, out)
    out = re.sub(r"(?i)(\s(?:href|src)\s*=\s*)'(.*?)'", repl_sq, out)

    def css_url(m: re.Match) -> str:
        q, val = (m.group(1) or ""), m.group(2)
        if _skip_proxy(val): return m.group(0)
        absu = _abs_url(base, val)
        if not _is_allowed_url(absu): return m.group(0)
        return f"url({q}{_asset_proxy(absu)}{q})"

    out = re.sub(r'(?i)url\(\s*([\'"]?)(?!data:)([^)\'"]+)\s*\)', css_url, out)
    out = re.sub(
        r'(?i)@import\s+([\'"])([^\'"]+)',
        lambda m: m.group(0) if _skip_proxy(m.group(2)) or not _is_allowed_url(_abs_url(base, m.group(2)))
        else f'@import {m.group(1)}{_asset_proxy(_abs_url(base, m.group(2)))}',
        out,
    )
    out = _inject_css_and_hooks(out)
    return out


# ───────────────────────── frame & assets ─────────────────────────

@bp.get("/embed/frame")
def proxy_frame():
    raw = request.args.get("url", "")
    target = unquote(raw).strip()
    if not _is_allowed_url(target): abort(400, "bad url")

    up = SESSION.get(target, timeout=TIMEOUT, headers={
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": request.headers.get("User-Agent", "Mozilla/5.0"),
        "Accept-Language": request.headers.get("Accept-Language", "de,en;q=0.9"),
    })

    ct = (up.headers.get("Content-Type") or "text/html; charset=utf-8").lower()

    # >>> Vectoplan: kein HTML-Rewrite, nur durchreichen <<<
    if "vectoplan.com" in _origin(target):
        return Response(up.content, 200, _std_headers(ct))

    if "text/html" in ct:
        try:
            html = _rewrite_html(up.text, target)
            return Response(html.encode("utf-8", "ignore"), 200, _std_headers("text/html; charset=utf-8"))
        except Exception:
            return Response(up.content, 200, _std_headers(ct))
    return Response(up.content, 200, _std_headers(ct))



@bp.get("/embed/a")
def proxy_asset():
    u = request.args.get("u", "")
    url = unquote(u).strip()
    if not _is_allowed_url(url): abort(400, "bad asset url")

    # Analytics komplett stummschalten
    try:
        hp = urlparse(url)
        if (hp.hostname or "").lower() == "analytics.vectoplan.com":
            return Response(b"", 204, _std_headers("text/plain; charset=utf-8"))
    except Exception:
        pass

    try:
        headers = {
            "Accept": "*/*",
            "User-Agent": request.headers.get("User-Agent", "Mozilla/5.0"),
            "Accept-Language": request.headers.get("Accept-Language", "de,en;q=0.9"),
            "Referer": _origin(url) + "/",
            **_auth_header_for(url),
        }
        up = SESSION.get(url, timeout=TIMEOUT, headers=headers)
    except Exception as ex:
        current_app.logger.exception("asset upstream error")
        abort(502, f"upstream error: {ex}")

    ct = up.headers.get("Content-Type", "application/octet-stream")
    body = up.content

    if ct.lower().startswith("text/css"):
        try:
            css = body.decode("utf-8", "ignore")
            def css_root(m: re.Match) -> str:
                q, pth = (m.group(1) or ""), m.group(2)
                absu = _origin(url) + pth
                return f"url({q}{_asset_proxy(absu)}{q})"
            css = re.sub(r'(?i)url\(\s*([\'"]?)(/[^)\'"]+)\s*\)', css_root, css)
            body = css.encode("utf-8"); ct = "text/css; charset=utf-8"
        except Exception:
            pass

    return Response(body, up.status_code or 200, _std_headers(ct))


@bp.route("/embed/x", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def proxy_any():
    """
    Proxy für GraphQL/REST (nicht-GET und GET mit Body). Ziel per ?u=...
    Hängt PAT an und spiegelt Upstream-Status.
    Zusätzlich:
      - analytics.vectoplan.com/track → 204
      - /api/products/get_project_overview_info → 200 JSON-Stub {success:false}
    """
    u = request.args.get("u", "")
    url = unquote(u).strip()
    if not _is_allowed_url(url): abort(400, "bad url")

    try:
        hp = urlparse(url)
        host = (hp.hostname or "").lower()
        if host == "analytics.vectoplan.com":
            return Response(b"", 204, _std_headers("text/plain; charset=utf-8"))
        if host.endswith("vectoplan.com") and "/api/products/get_project_overview_info" in (hp.path or ""):
            return jsonify({"success": False}), 200
    except Exception:
        pass

    try:
        method = request.method.upper()
        headers = {
            "Accept": request.headers.get("Accept", "*/*"),
            "User-Agent": request.headers.get("User-Agent", "Mozilla/5.0"),
            "Accept-Language": request.headers.get("Accept-Language", "de,en;q=0.9"),
            "Referer": _origin(url) + "/",
            **_auth_header_for(url),
        }
        ct_in = request.headers.get("Content-Type")
        if ct_in: headers["Content-Type"] = ct_in
        data = request.get_data() if method in {"POST", "PUT", "PATCH"} else None

        up = SESSION.request(method, url, headers=headers, data=data, timeout=TIMEOUT)
        if up.status_code >= 400:
            current_app.logger.warning("embed.x upstream %s %s -> %s: %s",
                                       method, url, up.status_code, (up.text or "")[:300])
        resp_ct = up.headers.get("Content-Type", "application/octet-stream")
        return Response(up.content, up.status_code or 200, _std_headers(resp_ct))
    except Exception as ex:
        current_app.logger.exception("proxy_any upstream error")
        abort(502, f"upstream error: {ex}")


# ───────────────────────── direkte Passthroughs (relative URLs) ─────────────────────────
# Diese Routen fangen die relativen Aufrufe des SPAs ab (sonst „Failed to fetch“).

@bp.route("/graphql", methods=["POST", "OPTIONS"])
def passthrough_graphql():
    url = "https://vectoplan.com/graphql"
    try:
        headers = {
            "Accept": request.headers.get("Accept", "application/json"),
            "User-Agent": request.headers.get("User-Agent", "Mozilla/5.0"),
            "Accept-Language": request.headers.get("Accept-Language", "de,en;q=0.9"),
            **_auth_header_for("vectoplan.com"),
        }
        ct = request.headers.get("Content-Type") or "application/json"
        headers["Content-Type"] = ct
        up = SESSION.request(request.method, url, headers=headers, data=request.get_data(), timeout=TIMEOUT)
        return Response(up.content, up.status_code or 200, _std_headers(up.headers.get("Content-Type", "application/json")))
    except Exception as ex:
        current_app.logger.exception("graphql passthrough error")
        abort(502, f"upstream error: {ex}")


@bp.route("/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def passthrough_api(path: str):
    # Speziell problematischer Endpunkt stubsicher:
    if path == "products/get_project_overview_info":
        return jsonify({"success": False}), 200
    url = urlunparse(("https", "vectoplan.com", f"/api/{path}", "", request.query_string.decode("utf-8"), ""))
    try:
        headers = {
            "Accept": request.headers.get("Accept", "*/*"),
            "User-Agent": request.headers.get("User-Agent", "Mozilla/5.0"),
            "Accept-Language": request.headers.get("Accept-Language", "de,en;q=0.9"),
            **_auth_header_for("vectoplan.com"),
        }
        ct = request.headers.get("Content-Type")
        if ct: headers["Content-Type"] = ct
        data = request.get_data() if request.method.upper() in {"POST", "PUT", "PATCH"} else None
        up = SESSION.request(request.method, url, headers=headers, data=data, timeout=TIMEOUT)
        return Response(up.content, up.status_code or 200, _std_headers(up.headers.get("Content-Type", "application/octet-stream")))
    except Exception as ex:
        current_app.logger.exception("api passthrough error")
        abort(502, f"upstream error: {ex}")


@bp.get("/preview/<path:path>")
def passthrough_preview(path: str):
    url = urlunparse(("https", "vectoplan.com", f"/preview/{path}", "", request.query_string.decode("utf-8"), ""))
    try:
        headers = {
            "Accept": "*/*",
            "User-Agent": request.headers.get("User-Agent", "Mozilla/5.0"),
            "Accept-Language": request.headers.get("Accept-Language", "de,en;q=0.9"),
            **_auth_header_for("vectoplan.com"),
        }
        up = SESSION.get(url, timeout=TIMEOUT, headers=headers)
        return Response(up.content, up.status_code or 200, _std_headers(up.headers.get("Content-Type", "application/octet-stream")))
    except Exception as ex:
        logging.exception("preview passthrough error"); abort(502, f"upstream error: {ex}")


@bp.get("/projects/<path:path>")
def passthrough_projects(path: str):
    """HTML-Seiten unter /projects/... (internes Routing des SPAs)."""
    url = urlunparse(("https", "vectoplan.com", f"/projects/{path}", "", request.query_string.decode("utf-8"), ""))
    try:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": request.headers.get("User-Agent", "Mozilla/5.0"),
            "Accept-Language": request.headers.get("Accept-Language", "de,en;q=0.9"),
            **_auth_header_for("vectoplan.com"),
        }
        up = SESSION.get(url, timeout=TIMEOUT, headers=headers)
    except Exception as ex:
        logging.exception("projects passthrough error"); abort(502, f"upstream error: {ex}")
    ct = (up.headers.get("Content-Type") or "text/html; charset=utf-8").lower()
    if "text/html" in ct:
        try:
            html = _rewrite_html(up.text, url)
            return Response(html.encode("utf-8", "ignore"), up.status_code or 200, _std_headers("text/html; charset=utf-8"))
        except Exception:
            logging.exception("projects html rewrite failed")
            return Response(up.content, up.status_code or 200, _std_headers(ct))
    return Response(up.content, up.status_code or 200, _std_headers(ct))


# Nuxt statics
@bp.get("/_nuxt/<path:path>")
def proxy_nuxt_root(path: str):
    url = urlunparse(("https", "vectoplan.com", f"/_nuxt/{path}", "", request.query_string.decode("utf-8"), ""))
    try:
        headers = {
            "Accept": "*/*",
            "User-Agent": request.headers.get("User-Agent", "Mozilla/5.0"),
            "Accept-Language": request.headers.get("Accept-Language", "de,en;q=0.9"),
            "Referer": "https://vectoplan.com/",
            **_auth_header_for("vectoplan.com"),
        }
        up = SESSION.get(url, timeout=TIMEOUT, headers=headers)
    except Exception as ex:
        logging.exception("nuxt root upstream error"); abort(502, f"upstream error: {ex}")
    ct = up.headers.get("Content-Type", "application/javascript")
    return Response(up.content, up.status_code or 200, _std_headers(ct))


@bp.get("/embed/<path:fname>")
def proxy_chunk(fname: str):
    url = urlunparse(("https", "vectoplan.com", f"/_nuxt/{fname}", "", request.query_string.decode("utf-8"), ""))
    try:
        headers = {
            "Accept": "application/javascript,*/*;q=0.8",
            "User-Agent": request.headers.get("User-Agent", "Mozilla/5.0"),
            "Accept-Language": request.headers.get("Accept-Language", "de,en;q=0.9"),
            "Referer": "https://vectoplan.com/",
            **_auth_header_for("vectoplan.com"),
        }
        up = SESSION.get(url, timeout=TIMEOUT, headers=headers)
    except Exception as ex:
        logging.exception("chunk upstream error"); abort(502, f"upstream error: {ex}")
    ct = up.headers.get("Content-Type", "application/javascript")
    return Response(up.content, up.status_code or 200, _std_headers(ct))
