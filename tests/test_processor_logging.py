import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import tempfile
import unittest

from backend_voices_data.utils import setup_logger


class ProcessorLoggingTests(unittest.TestCase):
    def close_logger(self, logger):
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_reinitialization_closes_old_file_and_keeps_two_handlers(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = setup_logger(directory, 'processor.log', 'TestBoundedProcessor')
            self.addCleanup(self.close_logger, logger)
            old_file = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
            setup_logger(directory, 'processor.log', 'TestBoundedProcessor')
            self.assertIsNone(old_file.stream)
            self.assertEqual(len(logger.handlers), 2)
            handler = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
            self.assertEqual(handler.maxBytes, 5 * 1024 * 1024)
            self.assertEqual(handler.backupCount, 2)
            self.close_logger(logger)

    def test_rotation_only_replaces_log_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            protected = {'data.json': b'{"keep": true}', 'image.svg': b'<svg/>',
                         'other.log': b'keep other logs'}
            for name, content in protected.items():
                (root / name).write_bytes(content)
            logger = setup_logger(directory, 'processor.log', 'TestRotatingProcessor')
            self.addCleanup(self.close_logger, logger)
            handler = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
            handler.maxBytes = 100
            for _ in range(10):
                logger.debug('x' * 60)
            self.close_logger(logger)
            self.assertEqual(len(list(root.glob('processor.log*'))), 3)
            self.assertFalse((root / 'processor.log.3').exists())
            for name, content in protected.items():
                self.assertEqual((root / name).read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
