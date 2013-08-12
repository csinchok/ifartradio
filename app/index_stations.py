from pyelasticsearch import ElasticSearch
from pyelasticsearch.exceptions import ElasticHttpNotFoundError, IndexAlreadyExistsError
import requests
import time


import settings


es = ElasticSearch(settings.ES_URL)
INDEX_NAME = settings.ES_INDEX

try:
	es.delete_index(INDEX_NAME)
except ElasticHttpNotFoundError:
	pass

try:
	es.create_index(INDEX_NAME)
except IndexAlreadyExistsError:
	pass

headers = {
	'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/28.0.1500.72 Safari/537.36'
}

failures = 0
pk = 0
while failures < 200:
	pk += 1
	r = requests.get("http://www.iheart.com/a/live/station/%d/" % pk, headers=headers)

	if r.status_code != 200:
		if r.status_code > 500:
			print("[%d] %d" % (r.status_code, pk))
			# in the case of a 500, let's try again.
			time.sleep(1)
			pk -= 1
		
		failures += 1
		continue

	if r.json().get('id') is None:
		failures += 1
		continue

	failures = 0
	es.index(INDEX_NAME, 'station', r.json(), id=r.json().get('id'))

print("Bailed after %d failures (pk %d)" % (failures, pk))