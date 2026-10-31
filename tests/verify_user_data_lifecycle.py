import pathlib
import subprocess
import shutil
import sqlite3
import time

install_root = pathlib.Path('D:/temp_installer_userdata_test').resolve()
if install_root.exists():
    shutil.rmtree(install_root, ignore_errors=True)
install_root.mkdir(parents=True, exist_ok=True)

installer = pathlib.Path('dist/SpotifyDownloader-v2.0.0-Setup.exe').resolve()

# 1. Fresh Install into test root
cmd1 = [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', f'/DIR={install_root}']
p1 = subprocess.run(cmd1, timeout=60)
assert p1.returncode == 0, 'Installation failed'

# 2. Populate real user data in data/, downloads/, and config/
data_dir = install_root / 'data'
downloads_dir = install_root / 'downloads'
config_dir = install_root / 'config'

data_dir.mkdir(parents=True, exist_ok=True)
downloads_dir.mkdir(parents=True, exist_ok=True)
config_dir.mkdir(parents=True, exist_ok=True)

# Create mock database with user table and row
db_file = data_dir / 'studio.db'
conn = sqlite3.connect(db_file)
conn.execute('CREATE TABLE user_library (id TEXT PRIMARY KEY, title TEXT, artist TEXT)')
conn.execute("INSERT INTO user_library VALUES ('t1', 'Bohemian Rhapsody', 'Queen')")
conn.commit()
conn.close()

# Create mock downloaded audio file
mock_mp3 = downloads_dir / 'Queen - Bohemian Rhapsody.mp3'
mock_mp3.write_bytes(b'ID3_MOCK_MP3_AUDIO_CONTENT_FOR_TESTING')

# Create custom config
cfg_file = config_dir / 'config.json'
cfg_file.write_text('{"custom_user_setting": 42, "output_format": "flac"}', encoding='utf-8')

print('User data fixtures successfully created:')
print('  Database:', db_file.exists())
print('  Audio file:', mock_mp3.exists(), 'Size:', mock_mp3.stat().st_size)
print('  Config file:', cfg_file.exists())

# 3. Perform Upgrade via Installer over existing install
p_upgrade = subprocess.run(cmd1, timeout=60)
assert p_upgrade.returncode == 0, 'Upgrade failed'

print('\nPost-Upgrade User Data Verification:')
assert db_file.exists(), 'Database missing after upgrade!'
conn = sqlite3.connect(db_file)
cur = conn.cursor()
cur.execute("SELECT title, artist FROM user_library WHERE id='t1'")
row = cur.fetchone()
conn.close()
print('  Database content intact:', row == ('Bohemian Rhapsody', 'Queen'))

assert mock_mp3.exists(), 'Audio file deleted after upgrade!'
print('  Downloaded audio file intact:', mock_mp3.read_bytes() == b'ID3_MOCK_MP3_AUDIO_CONTENT_FOR_TESTING')

assert cfg_file.exists(), 'Config file missing after upgrade!'
print('  Config file custom setting intact:', 'custom_user_setting' in cfg_file.read_text(encoding='utf-8'))

# 4. Perform Uninstallation via unins000.exe
unins = install_root / 'unins000.exe'
assert unins.exists(), 'Uninstaller not found'
p_uninst = subprocess.run([str(unins), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], timeout=60)
assert p_uninst.returncode == 0, 'Uninstall failed'

time.sleep(3)

print('\nPost-Uninstallation Residual Audit:')
exe_file = install_root / 'SpotifyDownloader.exe'
print('  Program executable removed:', not exe_file.exists())
print('  User database preserved:', db_file.exists())
print('  User downloads preserved:', mock_mp3.exists())
print('  User config preserved:', cfg_file.exists())

# Cleanup test directory
shutil.rmtree(install_root, ignore_errors=True)
print('\nTeardown complete.')
