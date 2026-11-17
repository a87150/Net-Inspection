"""IP lookup and the IP address plan on one page: evidence on top, plan below."""
from django.shortcuts import render
from django.views.decorators.http import require_GET

from net.devices.network.ipam import plan_report
from net.devices.network.observations import search


@require_GET
def ip_management(request):
    query = (request.GET.get("q") or "").strip()
    lookup = search(query) if query else None
    return render(request, "devices/ip_management.html", {
        "query": query,
        "lookup": lookup,
        "report": plan_report(),
    })
