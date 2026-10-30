import unittest
from unittest.mock import Mock, patch

import cloud_listing_cache as cache


class CloudListingCacheTests(unittest.TestCase):
    def test_expiry_scope_change_and_invalidation(self):
        state = {}
        loader = Mock(side_effect=[[1], [2], [3], [4]])
        with patch.object(cache.st, 'session_state', state), patch.object(cache.time, 'monotonic') as clock:
            clock.return_value = 100
            self.assertEqual(cache.get_listing('_image_page_listing', 'page1', loader), [1])
            self.assertEqual(cache.get_listing('_image_page_listing', 'page1', loader), [1])
            loader.assert_called_once()
            self.assertEqual(cache.get_listing('_image_page_listing', 'page2', loader), [2])
            self.assertEqual(len(state), 1)
            clock.return_value = 131
            self.assertEqual(cache.get_listing('_image_page_listing', 'page2', loader), [3])
            state['_image_total'] = {'count': 50}
            state['_source_excel_listing'] = {'value': []}
            state['unrelated'] = 'keep'
            cache.invalidate_cloud_listings()
            self.assertEqual(state, {'unrelated': 'keep'})
            self.assertEqual(cache.get_listing('_image_page_listing', 'page2', loader), [4])

    def test_failures_are_not_cached(self):
        state = {}
        loader = Mock(side_effect=[ValueError('denied'), []])
        with patch.object(cache.st, 'session_state', state):
            with self.assertRaises(ValueError):
                cache.get_listing('_source_excel_listing', 'bucket', loader)
            self.assertFalse(state)
            self.assertEqual(cache.get_listing('_source_excel_listing', 'bucket', loader), [])
