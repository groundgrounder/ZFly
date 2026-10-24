"""Ordering shared by the GTK group picker and proxy search."""
def group_names(proxies, mode="rule", query=""):
    groups = [name for name, proxy in proxies.items() if proxy.get("all") is not None
              and not proxy.get("hidden") and (name != "GLOBAL" or mode == "global")]
    order = proxies.get("GLOBAL", {}).get("all") or []
    groups = [name for name in order if name in groups] + [name for name in groups if name not in order]
    query = query.casefold()
    return [name for name in groups if not query or query in name.casefold()
            or any(query in member.casefold() for member in proxies[name].get("all", []))]
