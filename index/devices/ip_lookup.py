"""Read-only IP/MAC lookup over observations inspections already saved."""
from django.shortcuts import render
from django.views.decorators.http import require_GET

from net.devices.network.observations import search


@require_GET
def ip_lookup(request):
    query = (request.GET.get("q") or "").strip()
    result = search(query) if query else None
    return render(request, "devices/ip_lookup.html", {"query": query, "result": result})
