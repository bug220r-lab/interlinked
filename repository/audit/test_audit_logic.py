"""Pure metadata-classification tests; no source/library/cache access or writes."""
from pathlib import Path
import importlib.util
import unittest

spec=importlib.util.spec_from_file_location('audit_logic',Path(__file__).with_name('audit_media.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def info(status='LOCAL_AVAILABLE',file_id='1',volume='2'):
    return dict(status=status,fileId=file_id,volumeId=volume,bytes=5,mtime_ns=7)
def path(status='LOCAL_AVAILABLE',installed=False):
    return dict(metadata=info(status),installedAsset=installed)

class AuditTests(unittest.TestCase):
    def test_private_output_cannot_use_source_directory(self):
        self.assertTrue(m.output_inside_source(r'C:\clone\repository\audit\data',[r'C:\clone\repository']))
        self.assertTrue(m.output_inside_source(r'C:\clone\repository',[r'C:\clone\repository']))
        self.assertFalse(m.output_inside_source(r'C:\clone\work\private-audit',[r'C:\clone\repository']))
        self.assertFalse(m.output_inside_source(r'C:\clone\repository-other',[r'C:\clone\repository']))
    def test_exact_literal_is_availability_only(self):
        self.assertEqual(m.classify_reference({'kind':'absolute'},[path()],[]),'EXACT_LITERAL_PATH_AVAILABLE')
    def test_relative_exists_remains_candidate(self):
        self.assertEqual(m.classify_reference({'kind':'relative'},[path()],[]),'RELATIVE_PATH_CANDIDATE_AVAILABLE')
    def test_installed_asset_separate_from_user_alias(self):
        self.assertEqual(m.classify_reference({'kind':'absolute'},[path(installed=True)],[]),'EXACT_INSTALLED_ASSET_AVAILABLE')
    def test_unique_name_does_not_establish_identity(self):
        self.assertEqual(m.classify_reference({'kind':'absolute'},[path('MISSING')],[path()]),'SINGLE_BASENAME_CANDIDATE_UNVERIFIED')
    def test_multiple_names_do_not_establish_identity(self):
        self.assertEqual(m.classify_reference({'kind':'absolute'},[path('MISSING')],[path(),path()]),'MULTIPLE_BASENAME_CANDIDATES_UNVERIFIED')
    def test_missing_means_audited_paths_only(self):
        self.assertEqual(m.classify_reference({'kind':'absolute'},[path('MISSING')],[]),'NO_LOCAL_MATCH_IN_AUDITED_PATHS')
    def test_deferred_path_never_available(self):
        self.assertEqual(m.classify_reference({'kind':'absolute'},[path('DEFERRED_CLOUD_OR_REPARSE')],[]),'DEFERRED_CLOUD_OR_REPARSE')
    def test_deferred_basename_stays_deferred(self):
        self.assertEqual(m.classify_reference({'kind':'relative'},[path('MISSING')],[path('DEFERRED_CLOUD_OR_REPARSE')]),'BASENAME_CANDIDATE_DEFERRED')
    def test_identity_requires_both_local_nonzero_ids(self):
        self.assertTrue(m.file_identity(info(),info()))
        self.assertFalse(m.file_identity(info(),info(volume='3')))
        self.assertFalse(m.file_identity(info(file_id='0'),info(file_id='0')))
        self.assertFalse(m.file_identity(info(),info('DEFERRED_CLOUD_OR_REPARSE')))
    def test_metadata_drift_detected(self):
        self.assertTrue(m.metadata_matches(info(),dict(bytes=5,mtime_ns=7)))
        self.assertFalse(m.metadata_matches(info(),dict(bytes=5,mtime_ns=8)))
    def test_path_coverage_is_finite(self):
        self.assertEqual(m.alias_root(m.PREFIX+'Downloads/a.mp4'),'Downloads')
        self.assertIsNone(m.alias_root(m.PREFIX+'Downloads-extra/a.mp4'))
        self.assertIsNone(m.alias_root('Drive_C/Program Files/asset.mp4'))
    def test_cache_candidate_does_not_claim_actual_cache_or_identity(self):
        rel=m.PREFIX+'Downloads/a.mp4'
        ref=dict(sameBasenameCandidates=[dict(path=r'C:\Users\jlewi\Downloads\a.mp4',catalogPath=rel,metadata=info())])
        selected={rel.casefold():dict(libraryMetadata=info(),libraryMatchesCatalog=True)}
        row=m.candidate_locations(ref,selected)[0]
        self.assertEqual(row['cacheRelativePath'],'Files/'+rel)
        self.assertEqual(row['currentClientCacheExists'],'NOT_CHECKED')
        self.assertEqual(row['matchBasis'],'BASENAME_ONLY_IDENTITY_UNVERIFIED')
        self.assertIsNone(m.candidate_locations(ref,{})[0]['cacheRelativePath'])

if __name__=='__main__':unittest.main()
