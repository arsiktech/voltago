"""Review regressions: frozen public-feed flags and canonical scalar tariff windows."""
from datetime import datetime, timedelta, timezone
import unittest

from test_pilot_coverage import station, coverage, AS_OF, RECENT, REVISION

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
def ago(seconds):
    return (NOW - timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')

class PilotCoverageReviewRegressions(unittest.TestCase):
    def counts(self, row, **options):
        report = coverage.build_report({'stations':[row],'leads':[]}, mode='served-capture',
            as_of=AS_OF, observed_at=options.pop('observed_at', RECENT), source_revision=REVISION, **options)
        return report['regions']['valencia']['counts']

    def status_row(self, feed='endolla', checked_age=60, publication_age=60):
        row = station()
        row['live_status'] = {'valid':True, 'feed':feed, 'checked_at':ago(checked_age), 'published_at':ago(publication_age)}
        return row

    def test_public_validation_expires_at_180_seconds_without_cli_override(self):
        for feed in ['endolla','spirii']:
            for age, expected in [(179,1),(180,1),(181,0),(1800,0)]:
                with self.subTest(feed=feed, age=age):
                    self.assertEqual(self.counts(self.status_row(feed,age))['fresh_live_locations'], expected)

    def test_spirii_publication_expires_independently_at_300_seconds(self):
        for age, expected in [(299,1),(300,1),(301,0),(1800,0),(-1,0)]:
            with self.subTest(age=age):
                self.assertEqual(self.counts(self.status_row('spirii',60,age))['fresh_live_locations'], expected)
        row = self.status_row('spirii');row['live_status']['published_at']=None
        self.assertEqual(self.counts(row)['fresh_live_locations'],0)

    def test_caller_can_tighten_but_cannot_relax_public_feed_limits(self):
        self.assertEqual(self.counts(self.status_row(checked_age=179),live_max_age=60)['fresh_live_locations'],0)
        self.assertEqual(self.counts(self.status_row(checked_age=181),live_max_age=36000)['fresh_live_locations'],0)
        self.assertEqual(self.counts(self.status_row('spirii',1,301),live_max_age=36000)['fresh_live_locations'],0)

    def test_valid_when_captured_does_not_mean_valid_at_report_time(self):
        row=self.status_row(checked_age=300)
        self.assertEqual(self.counts(row,observed_at=ago(299))['fresh_live_locations'],0)

    def test_general_runtime_freshness_remains_distinct(self):
        row=station();row['last_seen']=ago(1800)
        self.assertEqual(self.counts(row)['fresh_live_locations'],1)

    def test_canonical_always_valid_scalar_window_is_not_conditional(self):
        row=station()
        row['pricing']=[{'price':0.0,'currency':'EUR','unit':'kWh','valid_from':None,'valid_until':None,'days':None,
                         'source':'Fixture','human':'Published scalar quote','label':'Free','is_free':True,'is_currently_valid':True}]
        result=self.counts(row)
        self.assertEqual(result['sourced_numeric_price_locations'],1)
        self.assertEqual(result['conditional_sourced_price_locations'],0)
        self.assertEqual(result['unknown_primary_price_locations'],0)
        row['pricing'][0]['days']=list(range(7))
        self.assertEqual(self.counts(row)['conditional_sourced_price_locations'],0)

    def test_moneyless_pricing_or_components_cannot_hide_unknown_price(self):
        for patch in [{'pricing':[{'currency':'EUR','unit':'kWh'}]},
                      {'tariff_details':{'components':[{'type':'ENERGY'}]}}]:
            with self.subTest(patch=patch):
                row=station();row.update(price_min=None,current_price_min=None,**patch)
                result=self.counts(row)
                self.assertEqual(result['conditional_sourced_price_locations'],0)
                self.assertEqual(result['unknown_primary_price_locations'],1)

    def test_explicit_restricted_priced_window_is_conditional(self):
        row=station();row.update(price_min=None,current_price_min=None)
        row['pricing']=[{'price':0.2,'currency':'EUR','unit':'kWh','valid_from':'22:00','valid_until':'06:00','days':[0,1,2,3,4]}]
        result=self.counts(row)
        self.assertEqual(result['conditional_sourced_price_locations'],1)
        self.assertEqual(result['unknown_primary_price_locations'],0)

    def test_public_feed_caps_match_canonical_constants(self):
        from scripts.public_live_status import STATUS_MAX_AGE, PUBLICATION_MAX_AGE
        self.assertEqual(coverage.PUBLIC_STATUS_MAX_AGE, STATUS_MAX_AGE)
        self.assertEqual(coverage.PUBLICATION_MAX_AGE, PUBLICATION_MAX_AGE)

    def test_endolla_uses_validation_not_old_optional_publication(self):
        row=self.status_row('endolla',60,36000)
        row['last_seen']=ago(36000)
        self.assertEqual(self.counts(row)['fresh_live_locations'],1)

    def test_unrecognized_day_shapes_do_not_crash_the_inventory(self):
        row=station();row['pricing']=[{'price':0.2,'days':[{'unknown':'shape'}]}]
        self.assertEqual(self.counts(row)['conditional_sourced_price_locations'],1)

    def test_unrestricted_monetary_components_are_structured_not_conditional(self):
        row=station();row.update(price_min=None,current_price_min=None,tariff_details={'components':[{'type':'ENERGY','price':0.2}]})
        result=self.counts(row)
        self.assertEqual(result['conditional_sourced_price_locations'],0)
        self.assertEqual(result['unknown_primary_price_locations'],0)

if __name__ == '__main__':
    unittest.main()
