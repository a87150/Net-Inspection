"""IP lookup and the IP address plan on one page: evidence on top, plan below."""
from django.shortcuts import render
from django.views.decorators.http import require_GET

from index.common.access import is_admin
from net.devices.network.ipam import plan_report
from net.devices.network.observations import search
from net.models import IpSubnet


@require_GET
def ip_management(request):
    query = (request.GET.get("q") or "").strip()
    lookup = search(query) if query else None
    return render(request, "ipam/ip_management.html", {
        "query": query,
        "lookup": lookup,
        "report": plan_report(),
        "subnet_modal": {
            "endpoint": "/api/v1/ip-subnets/",
            "can_administer": is_admin(request.user),
            "purposes": [
                {"value": value, "label": label}
                for value, label in IpSubnet.Purpose.choices
            ],
        },
    })
