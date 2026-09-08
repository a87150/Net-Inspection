"""SQL-paged analyses merged with task-snapshot placeholders, without roster writes.

Memory is O(roster + page), not O(analyses). Distinct option values remain complete.
Missing people are determined BEFORE user filtering: filtering out an existing
analysis never fabricates a missing-log row. Equal sort keys put real records
in primary-key order, then placeholders in frozen roster order.
"""
from functools import cached_property

from django.db.models import CharField, Count, Q
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast

from index.common.table_query import apply_record_table, apply_table_query, _record_value
from index.common.table_registry import project_record_definition
from index.common.table_options import include_active_filter_options


class PeopleResultRows:
    ordered = True

    def __init__(self, queryset, placeholders, *, sort_field=None, order='desc'):
        self.queryset = queryset
        self.placeholders = placeholders
        self.sort_field = sort_field or next(field for field in project_record_definition('computers').fields
                                            if field.key == 'created_at')
        self.order = order

    @classmethod
    def from_roster(cls, analyses, roster):
        from net.devices.pc.matching import join_analysis_rows
        identity = 'report_enrichment__personnel_id'
        analyses = analyses.filter(**{f'{identity}__in': [str(person['id']) for person in roster]})
        matched = set(analyses.order_by().annotate(
            _person_identity=Cast(KeyTextTransform('personnel_id', 'report_enrichment'), CharField()),
        ).values_list('_person_identity', flat=True).distinct())
        missing = [person for person in roster if str(person['id']) not in matched]
        placeholders = join_analysis_rows([], missing, 'people')
        for row in placeholders:
            row.report_enrichment = row.details['enrichment']
        return cls(analyses, placeholders)

    def apply_query(self, request, definition, *, prefix='', include_legacy_status=True):
        # Legacy callers can still pass the global PC definition (property paths).
        if not definition.record_semantics:
            definition = project_record_definition('computers')
        queryset, state = apply_table_query(request, self.queryset, definition, prefix=prefix,
                                             include_legacy_status=include_legacy_status)
        placeholders, placeholder_state = apply_record_table(request, self.placeholders, definition, prefix=prefix)
        for field in definition.fields:
            if field.option_mode not in {'distinct', 'suggest'} or not field.filterable:
                continue
            values = {value for options in (state['field_options'], placeholder_state['field_options'])
                      for value, label in options[field.key] if label == value}
            state['field_options'][field.key] = tuple((value, value) for value in sorted(values))
            state['field_option_modes'][field.key] = (
                'suggest' if field.option_mode == 'suggest' or len(values) > field.option_limit else 'distinct')
        include_active_filter_options(state['field_options'], state['field_option_modes'], state['filters'])
        sort_field = next(field for field in definition.fields if field.key == state['sort'])
        return type(self)(queryset, placeholders, sort_field=sort_field, order=state['order']), state

    @cached_property
    def _analysis_count(self):
        return self.queryset.count()

    def count(self):
        return self._analysis_count + len(self.placeholders)

    def __len__(self):
        return self.count()

    @cached_property
    def _positions(self):
        """Locate each placeholder using SQL counts, never by scanning analyses.

        Batch conditional aggregates so many placeholders sharing a department
        or NULL timestamp need only one rank expression. Comparison uses the
        database's own lookup/collation, matching the actual query ordering.
        """
        if not self.placeholders:
            return []
        source = self.sort_field.query_source or self.sort_field.source
        placeholders = sorted(self.placeholders,
            key=lambda row: (_record_value(row, self.sort_field) is None, _record_value(row, self.sort_field)),
            reverse=self.order == 'desc')
        values = list(dict.fromkeys(_record_value(row, self.sort_field) for row in placeholders))
        ranks = {}
        for start in range(0, len(values), 100):
            batch = values[start:start + 100]
            expressions = {}
            for index, value in enumerate(batch):
                null = Q(**{f'{source}__isnull': True})
                if value is None:
                    # Real NULLs precede placeholder NULLs within the tie.
                    condition = null if self.order == 'desc' else Q()
                elif self.order == 'desc':
                    condition = null | Q(**{f'{source}__gte': value})
                else:
                    condition = Q(**{f'{source}__lte': value})
                expressions[f'r{index}'] = Count('pk', filter=condition)
            counts = self.queryset.order_by().aggregate(**expressions)
            ranks.update((value, counts[f'r{index}']) for index, value in enumerate(batch))
        # Rank first also handles collations whose string order differs from Python.
        placeholders.sort(key=lambda row: ranks[_record_value(row, self.sort_field)])
        return [(ranks[_record_value(row, self.sort_field)] + index, row)
                for index, row in enumerate(placeholders)]

    def __getitem__(self, key):
        if isinstance(key, int):
            if key < 0:
                key += self.count()
            if key < 0 or key >= self.count():
                raise IndexError(key)
            return self[key:key + 1][0]
        if not isinstance(key, slice):
            raise TypeError('Expected an integer or slice')
        start, stop, step = key.indices(self.count())
        if step != 1:
            return [self[index] for index in range(start, stop, step)]
        if stop <= start:
            return []
        before = sum(position < start for position, _ in self._positions)
        inserted = {position: row for position, row in self._positions if start <= position < stop}
        offset = start - before
        length = stop - start - len(inserted)
        actual = iter(self.queryset[offset:offset + length])
        return [inserted[position] if position in inserted else next(actual) for position in range(start, stop)]

    def __iter__(self):
        # CSV consumes this iterator without QuerySet result caching. Only a
        # database-driver chunk and the frozen placeholders remain in memory.
        actual = iter(self.queryset.iterator(chunk_size=200))
        position = 0
        for placeholder_position, placeholder in self._positions:
            while position < placeholder_position:
                yield next(actual)
                position += 1
            yield placeholder
            position += 1
        yield from actual
