from pyelasticsearch import ElasticSearch
from pyelasticsearch.exceptions import ElasticHttpNotFoundError, IndexAlreadyExistsError
import requests
import time

IGNORED_GENRES = ("9", "15", "19")  # We only care about stations that play music.

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

	crawled_data = r.json()
	if crawled_data.get("primary_genre_id") in IGNORED_GENRES:
		continue  # We don't care about talk radio etc

	index_data = {
		"band": crawled_data.get("band"),
		"call_letters": crawled_data.get("call_letters"),
		"city": crawled_data.get("city"),
		"state": crawled_data.get("state"),
		"country": crawled_data.get("country"),
		"name": crawled_data.get("name"),
		"description": crawled_data.get("description"),
		"logo": crawled_data.get("logo"),
		"twitter": crawled_data.get("twitter"),
		"station_site": crawled_data.get("station_site"),
		"primary_genre": crawled_data.get("primary_genre"),
		"frequency": crawled_data.get("frequency"),
		"shoutcast_url": crawled_data.get("shoutcast_url"),
	}

	# TODO: get lat, lon
	if hasattr(settings, 'GEONAMES_USER') and settings.GEONAMES_USER != "demo":
		params = {
			"name_equals": index_data["city"],
			"country": index_data["country"],
			"adminCode1": index_data["state"],
			"maxRows": 10,
			"lang": "en",
			"username": settings.GEONAMES_USER,
			"style": "medium"
		}
		geo_request = requests.get("http://api.geonames.org/searchJSON", params=params)
		geonames = geo_request.json().get("geonames", [])
		if geonames:
			index_data["location"] = {
				"lat": float(geonames[0]["lat"]),
				"lon": float(geonames[0]["lng"])
			}

	es.index(INDEX_NAME, 'station', index_data, id=crawled_data.get('id'))

print("Bailed after %d failures (pk %d)" % (failures, pk))