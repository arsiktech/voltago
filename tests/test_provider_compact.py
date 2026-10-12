import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
row = {
    'id':'dgt:42',
    'name':'Station',
    'raw': {'operator': 'Iberdrola', 'unrelated': 'bulk'},
    # Captured API observation fixture; no invented live assertion.
    'owner': {'name':'ENDESA X WAY, S.L.', 'website':'www.endesaxway.com', 'phone':'800760255', 'logo':None},
}
result = server.compact_station_for_ui(row)
assert result.get('operator') == 'Iberdrola', result
assert 'raw' not in result, result
assert result.get('owner') == row['owner'], result
assert row['raw']['unrelated'] == 'bulk'

# Conflicting source-reported names are not safe branding. The compact surface must
# fail closed instead of retaining either source's operator text.
conflict = server.compact_station_for_ui({
    'id': 'dgt:conflict',
    'name': 'Conflict station',
    'operator': 'Iberdrola',
    'raw': {'operator': 'Endesa X Way'},
})
assert 'operator' not in conflict, conflict
assert 'raw' not in conflict, conflict
print('PASS compact promotes unambiguous operator provenance and removes raw bulk')
