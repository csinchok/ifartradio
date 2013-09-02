import requests
import tempfile
import struct
import pprint
import signal
import sys
import time
import datetime

from multiprocessing import Process, Queue

from pyelasticsearch import ElasticSearch

import settings

class StreamTitle(object):

    def __init__(self, stream_title):
        if stream_title[0] == "'" and stream_title[-1] == "'":
            stream_title = stream_title[1:-1]

        try:
            text_index = stream_title.index('text=')
        except ValueError:
            self.description = None
            self.data = {}
            return

        self.description = stream_title[:text_index].replace(" - ", "")
        stream_title = stream_title[text_index:]

        data = {}
        index = 0
        while index < len(stream_title):
            key = ""
            while stream_title[index] != '=':
                key += stream_title[index]
                index += 1

            data[key] = ""

            # Jump over the equals, and beginning quote
            index += 2
            while stream_title[index] != '"':
                data[key] += stream_title[index]
                index += 1

            if key[-2:].lower() == "id":
                try:
                    # Sometimes this is the string "null"
                    data[key] = int(data[key])
                except ValueError:
                    data[key] = None

            index += 2
        self.data = data

    def is_song(self):
        if self.description == "" and self.data.get('text') == '':
            return False
        # This is maybe a little hack, but it looks like if more than one ID is set, it's a real song.
        non_zero = 0
        for value in self.data.values():
            if isinstance(value, (int, long)) and value != 0:
                non_zero += 1
                if non_zero >= 2:
                    return True
        return False


class Worker(Process):

    def __init__(self, queue, number=-1):
        self.__queue = queue
        self.number = number
        self.name = "index_plays worker #%d" % number
        self.es = ElasticSearch(settings.ES_URL)
        Process.__init__(self)

    def parse_metadata(self, metadata):
        stream_info = metadata.split(";")
        for info in metadata.split(";"):
            key = info[:info.index('=')]
            value = info[info.index('=') + 1:]

            if key == 'StreamTitle':
                info = value[1:-1]


    def run(self):
        while 1:
            item = self.__queue.get()
            if item is None:
                break
            
            station_id, shoutcast_url, last_playing, last_playing_time = item
            r = requests.get(shoutcast_url, headers={'Icy-Metadata': '1'}, stream=True)
            
            # Parse the headers
            headers = {}
            line = ""
            for content in r.iter_content():
                line += content
                if line[-2:] == '\r\n':
                    # Line ended
                    if ":" in line:
                        key = line[:line.index(":")]
                        value = line[line.index(":") + 1:-2]
                        headers[key] = value

                    if len(line) == 2:
                        break
                    line = ""

            # We really need the metaint, so that we know where to look for metadata
            if 'icy-metaint' not in headers:
                print("No icy-metaint!")
                continue
            metaint = int(headers.get('icy-metaint'))

            data = r.raw.read(metaint)
            length = r.raw.read(1)
            length = struct.unpack('B', length)[0]
            metadata = r.raw.read(length * 16)
            r.close()
            # Now we've got the metadata string!
            if metadata != last_playing:
                stream_info = metadata.split(";")
                for info in metadata.split(";"):
                    try:
                        split_index = info.index('=')
                    except ValueError:
                        continue

                    key = info[:split_index]
                    value = info[split_index + 1:]

                    if key == 'StreamTitle':
                        s = StreamTitle(value)
                        if s.is_song():
                            print("[worker %s] %s : %s" % (self.number, s.description, s.data.get('text')))
                            doc = s.data
                            doc['description'] = s.description
                            self.es.index(settings.ES_INDEX, 'play', doc, parent=station_id)
                            last_playing_time = datetime.datetime.now()

            if last_playing_time is None:
                last_playing_time = datetime.datetime.now()

            # A valid station should be playing a song at least once every 20 minutes.
            if last_playing_time > (datetime.datetime.now() - datetime.timedelta(minutes=20)):
                # Send it around again....
                self.__queue.put((station_id, shoutcast_url, metadata, last_playing_time))

WORKERS_COUNT = 15
WORKERS = []

def handler(signum, frame):
    print 'Shutting down...'
    for worker in WORKERS:
        worker.terminate()
    sys.exit(signum)


if __name__ == '__main__':
    queue = Queue()

    signal.signal(signal.SIGTERM, handler)

    es = ElasticSearch(settings.ES_URL)

    play_mapping = {
        "play" : {
            "properties" : {
                "description" : {"type" : "string"},
                "text": {"type": "string"},
                "MediaBaseId": {"type": "long"},
                "TAID": {"type": "long"},
                "TPID": {"type": "long"},
                "amgArtistId": {"type": "long"},
                "amgTrackId": {"type": "long"},
                "cartcutId": {"type": "long"},
                "itunesTrackId": {"type": "long"},
                "song_spot": {"type": "string"}
            },
            "_timestamp": {
                "enabled": True
            },
            "_parent": {
                "type": "station"
            },
            "_ttl": {
                "enabled": True,
                "default" : "7d"
            }
        }
    }
    es.put_mapping(settings.ES_INDEX, 'play', play_mapping)

    query = {
        "query":{
            "filtered":{
                "query":{
                    "match_all":{}
                },
                "filter":{
                    "bool":{
                        "must":{
                            # "has_child": {
                            #     "type": "play",
                            #     "query": {
                            #         "match_all": {}
                            #     }
                            # }
                        },
                        "should":{},
                        "must_not":{
                            "missing":{
                                "field": "shoutcast_url",
                                "existence": True,
                                "null_value": True
                            }
                        }
                    }
                }
            }
        }
    }
    size = 50
    es_from = 0
    results = []
    while es_from == 0 or len(results) > 0:
        response = es.search(query, index=settings.ES_INDEX, doc_type='station', es_from=es_from, size=size)
        results = response['hits']['hits']
        for result in results:
            station_id = int(result['_id'])
            shoutcast_url = result['_source']['shoutcast_url']
            last_playing = "StreamTitle='';"
            queue.put((station_id, shoutcast_url, last_playing, None))
        es_from += size

    WORKERS = [] * WORKERS_COUNT
    for i in xrange(WORKERS_COUNT):
        worker = Worker(queue, number=i)
        WORKERS.append(worker)
        worker.start()
    
    while True:
        try:
            print(" *** queue size: %s ***" % queue.qsize())
        except NotImplementedError:
            pass
        time.sleep(60)
    
