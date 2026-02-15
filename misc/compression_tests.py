import zlib
import base64
import json
import cbor2

# ss = {"t":"cp","cid":5,"fc":"G7TAJ","ts":1767878063226,"p":"It start raining several hours ago here. No wind yet and not heavily raining. Not sure on pressure here yet but I hope we are going to be sheltered a bit by the Quantocks."}
# uncompressed_length = len(json.dumps(ss))
# compressed_bytes_length = len(zlib.compress(bytes(json.dumps(ss), 'utf-8'), 9))
# print(f"Uncompressed JSON: {uncompressed_length}")
# print(f"Compressed JSON Steve: {compressed_bytes_length} ({compressed_bytes_length / uncompressed_length:.0%})")
    
connect_strings = [
    {"t":"cp","cid":5,"fc":"G7TAJ","ts":1767878063226,"p":"It start raining several hours ago here. No wind yet and not heavily raining. Not sure on pressure"},
    {"t":"cp","cid":5,"fc":"G7TAJ","ts":1767878063226,"p":"It start raining several hours ago here. No wind yet and not heavily raining. Not sure on pressure here yet but I hope we are going to be sheltered a bit by the Quantocks."},
    {"t":"c","n":"Kevin","c":"M0AHN","lm":1761521344,"le":1761897876,"led":1761466013,"lhts":1761691762,"v":0.62,"cc":[{"cid":1,"lp":1761940431407,"le":1761251599948,"led":1761251814874},{"cid":0,"lp":1761995503633,"le":1761995503633,"led":1761995519738},{"cid":2,"lp":1760876112942,"le":1760875883350,"led":1760304800181}]},
    {"t":"c","n":"Kevin","c":"M0AHN","lm":1761521344,"le":1761897876,"led":1761466013,"lhts":1761691762,"v":0.62,"cc":[{"cid":1,"lp":1761940431407,"le":1761251599948,"led":1761251814874},{"cid":0,"lp":1761995503633,"le":1761995503633,"led":1761995519738},{"cid":2,"lp":1760876112942,"le":1760875883350,"led":1760304800181},{"cid":3,"lp":1761848955938,"le":1761652163958,"led":1761849039145},{"cid":4,"lp":1761986229744,"le":1761228326211,"led":1759513533597},{"cid":5,"lp":1762000935285,"le":1761993288162,"led":1761924818116},{"cid":6,"lp":1761762946083,"le":1758036643178,"led":1761734397646}]},
    {"t":"c","n":"Kevin","c":"M0AHN","lm":1761521344,"le":1761897876,"led":1761466013,"lhts":1761691762,"v":0.62,"cc":[{"cid":1,"lp":1761940431407,"le":1761251599948,"led":1761251814874},{"cid":0,"lp":1761995503633,"le":1761995503633,"led":1761995519738},{"cid":2,"lp":1760876112942,"le":1760875883350,"led":1760304800181},{"cid":3,"lp":1761848955938,"le":1761652163958,"led":1761849039145},{"cid":4,"lp":1761986229744,"le":1761228326211,"led":1759513533597},{"cid":5,"lp":1762000935285,"le":1761993288162,"led":1761924818116},{"cid":6,"lp":1761762946083,"le":1758036643178,"led":1761734397646},{"cid":7,"lp":1761166392544,"le":1760757602202,"led":1760518907924},{"cid":8,"lp":0,"le":0,"led":0},{"cid":9,"lp":1759180784225,"le":1759212888138,"led":1745741563724},{"cid":100,"lp":1761964812059,"le":1760722665484,"led":1761964836867},{"cid":10,"lp":1760082730309,"le":1760601745167,"led":1755630597896},{"cid":11,"lp":1761918800813,"le":1761779911500,"led":1761775449513},{"cid":12,"lp":1762004737707,"le":1761184708372,"led":1761184708372}]}
]

for connect_string in connect_strings:

    uncompressed_length = len(json.dumps(connect_string))
    compressed_bytes_length = len(zlib.compress(bytes(json.dumps(connect_string), 'utf-8'), 9))
    compressed_bytes_base64_length = len(base64.b64encode(zlib.compress(bytes(json.dumps(connect_string), 'utf-8'), 9)).decode('utf-8'))
    cbor_length = len(cbor2.dumps(connect_string))
    compressed_cbor_length = len(zlib.compress(cbor2.dumps(connect_string), 9))
    compressed_cbor_base64_length = len(base64.b64encode(zlib.compress(cbor2.dumps(connect_string), 9)).decode('utf-8'))

    print(f"Uncompressed JSON: {uncompressed_length}")
    print(f"Uncompressed CBOR: {cbor_length} ({cbor_length / uncompressed_length:.0%})")
    print(f"Compressed JSON: {compressed_bytes_length} ({compressed_bytes_length / uncompressed_length:.0%})")
    print(f"Compressed CBOR: {compressed_cbor_length} ({compressed_cbor_length / uncompressed_length:.0%})")
    print(f"Compressed JSON with base64: {compressed_bytes_base64_length} ({compressed_bytes_base64_length / uncompressed_length:.0%})")
    print(f"Compressed CBOR with base64: {compressed_cbor_base64_length} ({compressed_cbor_base64_length / uncompressed_length:.0%})")
    print("-----")