import sys
import argparse
from tango import Database, DbDevInfo, ConnectionFailed

# This script registers device servers for all paint tanks and mixing tank of one selected station
parser = argparse.ArgumentParser(
                    prog='register-server.py',
                    description='Register device server with Tango')
parser.add_argument('station_name', help='a name of the paint mixing station (e.g. station1)')

args = parser.parse_args()

#  reference to the Tango database
try:
    db = Database()
except ConnectionFailed as e:
    print("Error connecting to the Tango database:\n%s" % e)
    sys.exit(0)

for device_name in ["cyan", "magenta", "yellow", "black", "white", "mixer"]:
    device_info = DbDevInfo()
    # define the Tango Class served by this device server
    device_info._class = "PaintTank"
    # define the instance name for the device server
    device_info.server = "PaintMixingStation/%s" % args.station_name
    # define the device name
    device_info.name = "epfl/station1/%s" % device_name
    db.add_device(device_info)
    print("Added device: %s\tinstance: %s\tclass: %s" % (device_info.name, device_info.server, device_info._class))
