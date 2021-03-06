# encoding: utf-8

"""
Copyright (c) 2012 - 2016, Ernesto Ruge
All rights reserved.
Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import json
from datetime import datetime
from ..models import *
from ..base_task import BaseTask
from mongoengine.base.datastructures import BaseList


class ElasticsearchImport(BaseTask):
    name = 'ElasticsearchImport'
    services = [
        'mongodb',
        'elasticsearch'
    ]


    def __init__(self, body_id):
        self.body_id = body_id
        super().__init__()

    def __del__(self):
        pass

    def run(self, body_id, *args):
        if not (self.config.ENABLE_PROCESSING and self.config.ES_ENABLED):
            return
        self.body = Body.objects(uid=body_id).no_cache().first()
        if not self.body:
            return
        self.statistics = {
            'created': 0,
            'updated': 0
        }
        self.street_index()
        self.paper_location_index()
        self.paper_index()
        self.body = None
        self.es = None

    def street_index(self):

        if not self.es.indices.exists_alias(name='street-latest'):
            now = datetime.utcnow()
            index_name = 'street-' + now.strftime('%Y%m%d-%H%M')

            mapping = self.es_mapping_generator(Street, 'deref_street')

            mapping['properties']['autocomplete'] = {
                "type": 'text',
                "analyzer": "autocomplete_import_analyzer",
                "search_analyzer": "autocomplete_search_analyzer"
            }
            mapping['properties']['legacy'] = {
                'type': 'boolean'
            }


            self.es.indices.create(index=index_name, body={
                'settings': self.es_settings(),
                'mappings': {
                    'street': mapping
                }
            })

            self.es.indices.update_aliases({
                'actions': {
                    'add': {
                        'index': index_name,
                        'alias': 'street-latest'
                    }
                }
            })
        else:
            index_name = list(self.es.indices.get_alias('street-latest'))[0]

        for street in Street.objects(region=self.body.region).no_cache():
            street_dict = street.to_dict(deref='deref_street', format_datetime=True, delete='delete_street', clean_none=True)

            if 'geojson' in street_dict:
                if street_dict['geojson']:
                    if 'geometry' in street_dict['geojson']:
                        street_dict['geosearch'] = street_dict['geojson']['geometry']
                    else:
                        del street_dict['geojson']
                else:
                    del street_dict['geojson']
            if 'geojson' in street_dict:
                street_dict['geosearch'] = street_dict['geojson']['geometry']
                street_dict['geotype'] = street_dict['geojson']['geometry']['type']
                street_dict['geojson'] = json.dumps(street_dict['geojson'])

            street_dict['autocomplete'] = ''
            if 'streetName' in street_dict:
                if street_dict['streetName']:
                    street_dict['autocomplete'] = street_dict['streetName'] + ', '

            if 'postalCode' in street_dict:
                if street_dict['postalCode']:
                    street_dict['autocomplete'] += street_dict['postalCode'][0] + ' '

            if 'locality' in street_dict:
                if street_dict['locality']:
                    street_dict['autocomplete'] += street_dict['locality'][0]

            if 'subLocality' in street_dict:
                if street_dict['subLocality']:
                    street_dict['autocomplete'] += ' (' + street_dict['subLocality'][0] + ')'

            street_dict['legacy'] = bool(street.region.legacy)

            new_doc = self.es.index(
                index=index_name,
                id=str(street.id),
                doc_type='street',
                body=street_dict
            )

            if new_doc['result'] in ['created', 'updated']:
                self.statistics[new_doc['result']] += 1
            else:
                self.datalog.warn('Unknown result at %s' % street.id)
        self.datalog.info('ElasticSearch street import successfull: %s created, %s updated' % (
            self.statistics['created'], self.statistics['updated']))


    def paper_index(self):

        if not self.es.indices.exists_alias(name='paper-latest'):
            now = datetime.utcnow()
            index_name = 'paper-' + now.strftime('%Y%m%d-%H%M')

            mapping = self.es_mapping_generator(Paper, 'deref_paper')
            mapping['properties']['region'] = {
                'type': 'text'
            }


            self.es.indices.create(index=index_name, body={
                'settings': self.es_settings(),
                'mappings': {
                    'paper': mapping
                }
            })

            self.es.indices.update_aliases({
                'actions': {
                    'add': {
                        'index': index_name,
                        'alias': 'paper-latest'
                    }
                }
            })

        else:
            index_name = list(self.es.indices.get_alias('paper-latest'))[0]


        regions = []
        region = self.body.region
        while (region):
            regions.append(str(region.id))
            region = region.parent

        for paper in Paper.objects(body=self.body).no_cache():
            if paper.deleted:
                self.es.delete(
                    index=index_name,
                    id=str(paper.id),
                    doc_type='paper',
                    ignore=[400, 404]
                )
                continue
            paper_dict = paper.to_dict(deref='deref_paper', format_datetime=True, delete='delete_paper', clean_none=True)
            paper_dict['body_name'] = paper.body.name
            paper_dict['region'] = regions
            paper_dict['legacy'] = 'legacy' in paper_dict

            new_doc = self.es.index(
                index=index_name,
                id=str(paper.id),
                doc_type='paper',
                body=paper_dict
            )
            if new_doc['result'] in ['created', 'updated']:
                self.statistics[new_doc['result']] += 1
            else:
                self.datalog.warn('Unknown result at %s' % paper.id)
        self.datalog.info('ElasticSearch paper import successfull: %s created, %s updated' % (
            self.statistics['created'], self.statistics['updated']))

    def paper_location_index(self):

        if not self.es.indices.exists_alias(name='paper-location-latest'):
            now = datetime.utcnow()
            index_name = 'paper-location-' + now.strftime('%Y%m%d-%H%M')

            mapping = self.es_mapping_generator(Location, 'deref_paper_location')
            mapping['properties']['region'] = {
                'type': 'text'
            }
            mapping['properties']['legacy'] = {
                'type': 'boolean'
            }

            self.es.indices.create(index=index_name, body={
                'settings': self.es_settings(),
                'mappings': {
                    'location': mapping
                }
            })

            self.es.indices.update_aliases({
                'actions': {
                    'add': {
                        'index': index_name,
                        'alias': 'paper-location-latest'
                    }
                }
            })

        else:
            index_name = list(self.es.indices.get_alias('paper-location-latest'))[0]

        regions = []
        region = self.body.region
        while (region):
            regions.append(str(region.id))
            region = region.parent

        for location in Location.objects(body=self.body).no_cache():
            if location.deleted:
                self.es.delete(
                    index=index_name,
                    id=str(location.id),
                    doc_type='location',
                    ignore=[400, 404]
                )
                continue
            location_dict = location.to_dict(deref='deref_paper_location', format_datetime=True, delete='delete_paper_location', clean_none=True)
            location_dict['region'] = regions

            if 'geojson' in location_dict:
                if location_dict['geojson']:
                    if 'geometry' in location_dict['geojson']:
                        location_dict['geosearch'] = location_dict['geojson']['geometry']
                        if 'paper' in location_dict:
                            if type(location_dict['paper']) is list:
                                if 'properties' not in location_dict['geojson']:
                                    location_dict['geojson']['properties'] = {}
                                if not len(location_dict['paper']):
                                    continue
                                location_dict['geojson']['properties']['paper-count'] = len(location_dict['paper'])
                            else:
                                continue
                        else:
                            continue
                    else:
                        del location_dict['geojson']
                else:
                    del location_dict['geojson']
            if 'geojson' in location_dict:
                location_dict['geosearch'] = location_dict['geojson']['geometry']
                location_dict['geotype'] = location_dict['geojson']['geometry']['type']
                location_dict['geojson'] = json.dumps(location_dict['geojson'])

            location_dict['legacy'] = bool(location.region.legacy)

            new_doc = self.es.index(
                index=index_name,
                id=str(location.id),
                doc_type='location',
                body=location_dict
            )
            if new_doc['result'] in ['created', 'updated']:
                self.statistics[new_doc['result']] += 1
            else:
                self.datalog.warn('Unknown result at %s' % location.id)
        self.datalog.info('ElasticSearch paper-location import successfull: %s created, %s updated' % (
        self.statistics['created'], self.statistics['updated']))

    def es_mapping_generator(self, base_object, deref=None, nested=False, delete=None):
        mapping = {}
        for field in base_object._fields:
            if delete:
                if hasattr(base_object._fields[field], delete):
                    continue
            if base_object._fields[field].__class__.__name__ == 'ListField':
                if base_object._fields[field].field.__class__.__name__ == 'ReferenceField':
                    if getattr(base_object._fields[field].field, deref):
                        mapping[field] = self.es_mapping_generator(base_object._fields[field].field.document_type,
                                                                   deref, True)
                    else:
                        mapping[field] = self.es_mapping_field_object()
                else:
                    mapping[field] = self.es_mapping_field_generator(base_object._fields[field].field)
                    if mapping[field] == None:
                        del mapping[field]
            elif base_object._fields[field].__class__.__name__ == 'ReferenceField':
                if getattr(base_object._fields[field], deref):
                    mapping[field] = self.es_mapping_generator(base_object._fields[field].document_type, deref, True)
                else:
                    mapping[field] = self.es_mapping_field_object()
            elif hasattr(base_object._fields[field], 'geojson'):
                mapping['geosearch'] = {
                    'type': 'geo_shape'
                }
                mapping['geojson'] = {
                    'type': 'text'
                }
                mapping['geotype'] = {
                    'type': 'keyword'
                }
            else:
                mapping[field] = self.es_mapping_field_generator(base_object._fields[field])

            if not mapping[field]:
                del mapping[field]

        mapping = {
            'properties': mapping
        }
        if nested:
            mapping['type'] = 'nested'
        return mapping

    def es_mapping_field_generator(self, field):
        result = {'store': True}
        if field.__class__.__name__ == 'ObjectIdField':
            result['type'] = 'text'
        elif field.__class__.__name__ == 'IntField':
            result['type'] = 'integer'
        elif field.__class__.__name__ == 'DateTimeField':
            result['type'] = 'date'
            if field.datetime_format == 'datetime':
                result['format'] = 'date_hour_minute_second'
            elif field.datetime_format == 'date':
                result['format'] = 'date'
        elif field.__class__.__name__ == 'StringField':
            result['fields'] = {}
            result['type'] = 'text'
            if hasattr(field, 'fulltext'):
#                result['index'] = 'analyzed'
                result['analyzer'] = 'default_analyzer'
#            else:
#                result['index'] = 'not_analyzed'
            if hasattr(field, 'sortable'):
                result['fields']['sort'] = {
                    'type': 'text',
                    'analyzer': 'sort_analyzer',
                    'fielddata': True
                }
        elif field.__class__.__name__ == 'BooleanField':
            result['type'] = 'boolean'
        else:
            return None
        return result

    def es_mapping_field_object(self):
        return {
            'fielddata': True,
            'type': 'text'
        }

    def es_settings(self):
        return {
            'index': {
                'max_result_window': 250000,
#                'mapping': {
#                    'nested_fields': {
#                        'limit': 500
#                    },
#                    'total_fields': {
#                        'limit': 2500
#                    }
#                },
                'analysis': {
                    'filter': {
                        'german_stop': {
                            "type": 'stop',
                            "stopwords": '_german_'
                        },
                        'german_stemmer': {
                            "type": 'stemmer',
                            "language": 'light_german'
                        },
                        'custom_stop': {
                            "type": 'stop',
                            'stopwords': self.generate_stopword_list()
                        }
                    },
                    'char_filter': {
                        'sort_char_filter': {
                            'type': 'pattern_replace',
                            'pattern': '"',
                            'replace': ''
                        }
                    },
                    'tokenizer': {
                        'autocomplete': {
                            "type": "edge_ngram",
                            "min_gram": 2,
                            "max_gram": 10,
                            "token_chars": [
                                "letter"
                            ]
                        }
                    },
                    'analyzer': {
                        # Der Standard-Analyzer, welcher case-insensitive Volltextsuche bietet
                        'default_analyzer': {
                            'type': 'custom',
                            'tokenizer': 'standard',
                            'filter': [
                                'standard',
                                'lowercase',
                                'custom_stop',
                                'german_stop',
                                'german_stemmer'
                            ]
                        },
                        'sort_analyzer': {
                            'tokenizer': 'keyword',
                            'filter': [
                                'lowercase',
                                'asciifolding',
                                'custom_stop',
                                'german_stop',
                                'german_stemmer'
                            ],
                            'char_filter': [
                                'sort_char_filter'
                            ]
                        },
                        'suggest_import_analyzer': {
                            'tokenizer': 'keyword',
                            'filter': [
                                'lowercase',
                                'asciifolding',
                                'custom_stop',
                                'german_stop',
                                'german_stemmer'
                            ],
                            'char_filter': [
                                'html_strip'
                            ]
                        },
                        # Analyzer für die Live-Suche. Keine Stopwords, damit z.B. die -> diesel funktioniert
                        'suggest_search_analyzer': {
                            'tokenizer': 'keyword',
                            'filter': [
                                'lowercase',
                                'asciifolding',
                                'german_stemmer'
                            ],
                            'char_filter': [
                                'html_strip'
                            ]
                        },
                        'autocomplete_import_analyzer': {
                            'tokenizer': 'autocomplete',
                            "filter": [
                                "lowercase"
                            ]
                        },
                        'autocomplete_search_analyzer': {
                            "tokenizer": "lowercase"
                        }
                    }
                }
            }
        }

    def generate_stopword_list(self):
        return []
