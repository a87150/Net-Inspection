import json
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from net.infrastructure.sanitization import sanitize
from net.models import NetworkTopologyInterface, NetworkTopologyLink, NetworkTopologyObservation, TopologyDiscoveryBatch
from net.topology.read_model import current_topology_payload, interface_payload, link_payload


def _page(request, queryset, serializer):
    try: page_size=max(1,min(int(request.GET.get('page_size',50)),500))
    except (TypeError,ValueError):page_size=50
    try: page_number=max(1,int(request.GET.get('page',1)))
    except (TypeError,ValueError):page_number=1
    paginator=Paginator(queryset,page_size)
    page=paginator.get_page(page_number)
    return JsonResponse({'results':[serializer(row) for row in page.object_list],
        'pagination':{'page':page.number,'page_size':page_size,'count':paginator.count,'pages':paginator.num_pages}})


@login_required
@require_GET
def topology_data(request):
    return JsonResponse(current_topology_payload(include_stale=request.GET.get('include_stale')=='1',limit=request.GET.get('limit',1000)))


@login_required
@require_GET
def topology_interfaces(request):
    queryset=NetworkTopologyInterface.objects.select_related('device').order_by('device_id','stable_key')
    if request.GET.get('device_id'):queryset=queryset.filter(device_id=request.GET['device_id'])
    return _page(request,queryset,interface_payload)


@login_required
@require_GET
def topology_links(request):
    queryset=NetworkTopologyLink.objects.select_related('local_interface').order_by('-last_seen_at','id')
    if request.GET.get('include_stale')!='1':queryset=queryset.filter(status='current')
    if request.GET.get('device_id'):
        queryset=queryset.filter(local_interface__device_id=request.GET['device_id']) | queryset.filter(remote_device_id=request.GET['device_id'])
    return _page(request,queryset.distinct(),link_payload)


@login_required
@require_GET
def topology_batches(request):
    queryset=TopologyDiscoveryBatch.objects.order_by('-started_at','id')
    if request.GET.get('device_id'):queryset=queryset.filter(device_id=request.GET['device_id'])
    def serialize(row):
        return {'id':str(row.pk),'source_task_id':str(row.source_task_id),'source_target_id':str(row.source_target_id),
                'device_id':str(row.device_id),'protocol':row.protocol,'status':row.status,'schema_version':row.schema_version,
                'started_at':row.started_at.isoformat(),'collected_at':row.collected_at.isoformat() if row.collected_at else None,
                'finished_at':row.finished_at.isoformat() if row.finished_at else None,'message':row.message,
                'interface_count':row.interface_count,'observation_count':row.observation_count,
                'resolved_count':row.resolved_count,'unresolved_count':row.unresolved_count,'conflict_count':row.conflict_count}
    return _page(request,queryset,serialize)


@login_required
@require_GET
def topology_evidence(request, pk):
    if not request.user.has_perm('net.view_topology_evidence'):
        return HttpResponseForbidden('需要拓扑原始证据查看权限。')
    row=get_object_or_404(NetworkTopologyObservation,pk=pk)
    try:evidence=json.loads(row.evidence)
    except (TypeError,ValueError):evidence=row.evidence
    safe=sanitize(evidence)
    response=JsonResponse({'id':str(row.pk),'batch_id':str(row.batch_id),'protocol':row.protocol,
                           'neighbor':sanitize(row.neighbor),'evidence':json.dumps(safe,ensure_ascii=False)[:65536],
                           'evidence_sha256':row.evidence_sha256,'collected_at':row.collected_at.isoformat()})
    response['Cache-Control']='no-store'
    return response
