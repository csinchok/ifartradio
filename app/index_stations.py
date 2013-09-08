from pyelasticsearch import ElasticSearch
from pyelasticsearch.exceptions import ElasticHttpNotFoundError, IndexAlreadyExistsError
import requests
import time
import json
import StringIO

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

STATION_MAPPING = {
    "station" : {
        "properties" : {
            "band" : {"type" : "string", "index" : "not_analyzed"},
			"call_letters": {"type" : "string", "index" : "not_analyzed"},
			"city": {"type" : "string"},
			"state": {"type" : "string"},
			"country": {"type" : "string"},
			"name": {"type" : "string"},
			"description": {"type" : "string"},
			"logo": {"type" : "string"},
			"twitter": {"type" : "string"},
			"station_site": {"type" : "string"},
			"primary_genre": {"type" : "string", "index" : "not_analyzed"},
			"frequency": {"type" : "string"},
			"shoutcast_url": {"type" : "string"},
			"location": {"type" : "geo_point"},
			"geojson": {"type" : "string", "index" : "no"},
        }
    }
}
es.put_mapping(INDEX_NAME, "station", STATION_MAPPING)


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

	if crawled_data['call_letters'].endswith("-FM"):
		crawled_data["call_letters"] = crawled_data["call_letters"].replace("-FM", "")

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

	# Now with this hack, we'll get the station coverage area from the FCC. I hope.
	if index_data['band'].endswith("FM"):
		fcc_data = {
			"call": index_data["call_letters"],
			"serv": "FM",
			"fre2": index_data["frequency"],
			"list": "4",
			"NS": "N",
			"EW": "W",
			"size": "9"
		}
		r = requests.get("http://transition.fcc.gov/fcc-bin/fmq", params=fcc_data)
		if r.status_code != 200:
			print("Couldn't get %s: FMQ request error" % crawled_data.get("call_letters"))
			continue
		
		data = r.content.strip().split("|")
		if len(data) != 39:
			print("Couldn't get %s: FMQ data error" % crawled_data.get("call_letters"))
			continue
		fcc_data.update({
			'city': data[10].strip().replace(" ", "_"),
			'state': data[11].strip(),
			'dbu': 54,
			'appid': data[-2].strip(),
			"freq": index_data["frequency"]
		})

		KML_URL_FORMAT = "http://transition.fcc.gov/fcc-bin/contourplot.kml?appid=%(appid)s=982647&call=%(call)s&freq=%(freq)s&contour=%(dbu)s&city=%(city)s&state=%(state)s&.kml"
		r = requests.get(KML_URL_FORMAT % fcc_data)
		if r.status_code != 200:
			print("Couldn't get %s: KML request error" % crawled_data.get("call_letters"))
			continue

		kml_file = StringIO.StringIO(r.content)
		r = requests.post("http://ogre.adc4gis.com/convert", files={"upload": kml_file})
		if r.status_code != 200:
			print("Couldn't get %s: geojson conversion error" % crawled_data.get("call_letters"))
			continue

		geojson = r.json()
		geojson["features"].pop()  # Remove "Alternate color"
		coords = geojson["features"][0]["geometry"]["coordinates"]
		index_data["location"] = {
			"lat": float(coords[1]),
			"lon": float(coords[0])
		}
		index_data["geojson"] = json.dumps(geojson)


	es.index(INDEX_NAME, 'station', index_data, id=crawled_data.get('id'))

print("Bailed after %d failures (pk %d)" % (failures, pk))
