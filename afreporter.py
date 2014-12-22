# -*- coding: utf-8 -*-
# Copyright 2013 Alex Zaddach (mrzmanwiki@gmail.com)

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import sys
from irc.bot import SingleServerIRCBot
from wikitools import *
import settings
import threading
import thread
import time
import MySQLdb
import urllib
import os
import traceback
import re
import datetime
import json

connections = {}
IRCActive = False
LogActive = False

site = wiki.Wiki()
site.setMaxlag(-1)
site.login(settings.bot, settings.botpass)
AIV = page.Page(site, 'Wikipedia:Administrator intervention against vandalism/TB2')

#TODO: Abuse log now available on Wikimedia IRC

class timedTracker(dict):
	def __init__(self, args={}, expiry=300):
		dict.__init__(self, args)
		self.expiry = expiry
		self.times = set()
		self.times = set([(item, int(time.time())) for item in self.keys()])
		
	def __purgeExpired(self):
		checktime = int(time.time())-self.expiry
		removed = set([item for item in self.times if item[1] < checktime])
		self.times.difference_update(removed)
		[dict.__delitem__(self, item[0]) for item in removed]
		
	def __getitem__(self, key):
		self.__purgeExpired()
		if not key in self:
			return 0
		return dict.__getitem__(self, key)
	
	def __setitem__(self, key, value):
		self.__purgeExpired()
		if not key in self:
			self.times.add((key, int(time.time())))
		return dict.__setitem__(self, key, value)
	
	def __delitem__(self, key):
		self.times = set([item for item in self.times if item[0] != key])
		self.__purgeExpired()
		return dict.__delitem__(self, key)
	
	def __contains__(self, key):
		self.__purgeExpired()
		return dict.__contains__(self, key)
	
	def __repr__(self):
		self.__purgeExpired()
		return dict.__repr__(self)
		
	def __str__(self):
		self.__purgeExpired()
		return dict.__str__(self)
	
	def keys(self):
		self.__purgeExpired()
		return dict.keys(self)
	
class CommandBot(SingleServerIRCBot):

	def __init__(self, channel, nickname, server, port=6667):
		SingleServerIRCBot.__init__(self, [(server, port)], nickname, nickname)
		self.channel = channel

	def on_nicknameinuse(self, c, e):
		thread.interrupt_main()

	def on_welcome(self, c, e):
		global connections, IRCActive
		c.privmsg("NickServ", "identify "+settings.ircpass)
		time.sleep(3)
		c.join(self.channel)
		connections['command'] = c
		IRCActive = True
		return

class BotRunnerThread(threading.Thread):
	def __init__(self, bot):
		threading.Thread.__init__(self)
		self.bot = bot
		
	def run(self):
		self.bot.start()

def sendToChannel(msg):
	connections['command'].privmsg("#wikipedia-en-abuse-log", msg)
	
class StartupChecker(threading.Thread):
	def run(self):
		global IRCActive, LogActive
		time.sleep(60)
		if not IRCActive or not LogActive:
			print "Init fail"
			thread.interrupt_main()
	
immediate = set() 
vandalism = set()
useAPI = False

def checklag():
	global connections, useAPI
	waited = False
	try:
		testdb = MySQLdb.connect(db='enwiki_p', host="enwiki.labsdb", read_default_file="/data/project/zbot/replica.my.cnf")
		testcursor = testdb.cursor()
	except: # server down
		useAPI = True
		return False
	while True:
		# Check replag
		testcursor.execute('SELECT UNIX_TIMESTAMP() - UNIX_TIMESTAMP(rc_timestamp) FROM recentchanges ORDER BY rc_timestamp DESC LIMIT 1')
		replag = testcursor.fetchone()[0]
		# Fallback to API if replag is too high
		if replag > 300 and not useAPI:
			useAPI = True
			sendToChannel("Labs replag too high, using API fallback")
		if replag < 120 and useAPI:
			sendToChannel("Using Labs database")
			useAPI = False
		# Check maxlag if we're using the API
		if useAPI:
			params = {'action':'query',
				'meta':'siteinfo',
				'siprop':'dbrepllag'
			}
			req = api.APIRequest(site, params)
			res = req.query(False)
			maxlag = res['query']['dbrepllag'][0]['lag']
			# If maxlag is too high, just stop
			if maxlag > 600 and not waited:
				waited = True
				sendToChannel("Server lag too high, stopping reports")
			if waited and maxlag > 120:
				time.sleep(120)
				continue
		break			
	if waited:
		sendToChannel("Restarting reports")
		return True
	return False

db = MySQLdb.connect(db='enwiki_p', host="enwiki.labsdb", read_default_file="/data/project/zbot/replica.my.cnf")
db.autocommit(True)
cursor = db.cursor()
	
def getStart():
	if useAPI:
		params = {'action':'query',
			'list':'abuselog',
			'aflprop':'ids|timestamp',
			'afllimit':'1',
		}
		req = api.APIRequest(site, params)
		res = req.query(False)
		row = res['query']['abuselog'][0]
		lasttime = row['timestamp']
		lastid = row['id']
	else:
		cursor.execute('SELECT afl_timestamp, afl_id FROM abuse_filter_log ORDER BY afl_id DESC LIMIT 1')
		(lasttime, lastid) = cursor.fetchone()
	return (lasttime, lastid)
	
def normTS(ts): # normalize a timestamp to the API format
	ts = str(ts)
	if 'Z' in ts:
		return ts
	ts = datetime.datetime.strptime(ts, "%Y%m%d%H%M%S")
	return ts.strftime("%Y-%m-%dT%H:%M:%SZ")
	
def logFromAPI(lasttime):
	lasttime = normTS(lasttime)
	params = {'action':'query',
		'list':'abuselog',
		'aflstart':lasttime,
		'aflprop':'ids|user|action|title|timestamp',
		'afllimit':'50',
		'afldir':'newer',
	}
	req = api.APIRequest(site, params)
	res = req.query(False)	
	rows = res['query']['abuselog']
	if len(rows) > 0:
		del rows[0] # The API uses >=, so the first row will be the same as the last row of the last set
	ret = []
	for row in rows:
		entry = {}
		entry['l'] = row['id']
		entry['a'] = row['action']
		entry['ns'] = row['ns']
		p = page.Page(site, row['title'], check=False)
		entry['t'] = p.unprefixedtitle
		entry['u'] = row['user']
		entry['ts'] = row['timestamp']
		entry['f'] = str(row['filter_id'])
		ret.append(entry)
	return ret	
	
def logFromDB(lastid):
	query = """SELECT SQL_NO_CACHE afl_id, afl_action, afl_namespace, afl_title, 
	afl_user_text, afl_timestamp, afl_filter FROM abuse_filter_log
	WHERE afl_id > %s ORDER BY afl_id ASC""" % lastid
	rowcount = cursor.execute(query)
	ret = []
	res = cursor.fetchall()
	for row in res:
		entry = {}
		entry['l'] = row[0]
		entry['a'] = row[1]
		entry['ns'] = row[2]
		print row
		p = page.Page(site, row[3], check=False, namespace=row[2])
		entry['t'] = p.unprefixedtitle
		entry['u'] = row[4]
		entry['ts'] = row[5]
		entry['f'] = row[6]
		ret.append(entry)
	return ret	
	
def main():
	global connections, LogActive
	sc = StartupChecker()
	sc.start()
	getLists()
	if not immediate or not vandalism:
		raise Exception("Lists not initialised")
	listcheck = time.time()
	Cchannel = "#wikipedia-en-abuse-log"
	Cserver = "irc.freenode.net"
	nickname = "MrZ-bot"
	cbot = CommandBot(Cchannel, nickname, Cserver)
	cThread = BotRunnerThread(cbot)
	cThread.daemon = True
	cThread.start()
	while len(connections) != 1:
		time.sleep(2)
	time.sleep(5)
	checklag()
	lagcheck = time.time()
	IRCut = timedTracker() # user tracker for IRC
	AIVut = timedTracker() # user tracker for AIV
	IRCreported = timedTracker(expiry=60)
	AIVreported = timedTracker(expiry=600)
	titles = timedTracker() # this only reports to IRC for now
	(lasttime, lastid) = getStart()
	LogActive = True
	while True:
		if time.time() > listcheck+300:
			getLists()
			listcheck = time.time()
		if time.time() > lagcheck+600:
			lag = checklag()
			lagcheck = time.time()
			if lag:
				db.ping()
				(lasttime, lastid) = getStart()
		if useAPI:
			rows = logFromAPI(lasttime)
		else:
			rows = logFromDB(lastid)
		attempts = []
		for row in rows:
			logid = row['l']
			if logid <= lastid:
				continue
			action = row['a']
			ns = row['ns']
			title = row['t']
			filter = row['f']
			timestamp = row['ts']
			u = user.User(site, row['u'], check=False)
			username = u.name.encode('utf8')			
			# Check against 'immediate' list before doing anything
			if filter in immediate and not username in AIVreported:
				reportUser(u, filter=filter, hit=logid)
				AIVreported[username] = 1
			# Prevent multiple hits from the same edit attempt
			if (username, timestamp) in attempts:
				continue
			attempts.append((username, timestamp))
			# IRC reporting checks
			IRCut[username]+=1
			# 5 hits in 5 mins
			if IRCut[username] == 5 and not username in IRCreported:
				sendToChannel("!alert - [[User:%s]] has tripped 5 filters within the last 5 minutes: "\
				"http://en.wikipedia.org/wiki/Special:AbuseLog?wpSearchUser=%s"\
				%(username, urllib.quote(username)))
				del IRCut[username]
				IRCreported[username] = 1
			# Hits on pagemoves
			if action == 'move':
				sendToChannel("!alert - [[User:%s]] has tripped a filter doing a pagemove"\
				": http://en.wikipedia.org/wiki/Special:AbuseLog?details=%s"\
				%(username, str(logid)))
			# Frequent hits on one article, would be nice if there was somewhere this could
			# be reported on-wiki
			titles[(ns,title)]+=1
			if titles[(ns,title)] == 10 and not (ns,title) in IRCreported:
				p = page.Page(site, title, check=False, followRedir=False, namespace=ns)
				sendToChannel("!alert - 10 filters in the last 5 minutes have been tripped on [[%s]]: "\
				"http://en.wikipedia.org/wiki/Special:AbuseLog?wpSearchTitle=%s"\
				%(p.title.encode('utf8'), p.urltitle))
				del titles[(ns,title)]
				IRCreported[(ns,title)] = 1
			# AIV reporting - check if the filter is in one of the lists
			if filter not in vandalism.union(immediate):
				continue
			AIVut[username]+=1			
			# 10 hits in 5 minutes
			if AIVut[username] == 10 and not username in AIVreported:
				del AIVut[username]
				reportUser(u)
				AIVreported[username] = 1
		if rows:
			rows.reverse()
			last = rows[0]
			lastid = last['l']
			lasttime = last['ts']
		time.sleep(1.5)


def reportUser(u, filter=None, hit=None):
	if u.isBlocked():
		return
	username = u.name.encode('utf8')
	if filter:
		name = filterName(filter)
		reason = "Tripped [[Special:AbuseFilter/%(f)s|filter %(f)s]] (%(n)s) "\
		"([{{fullurl:Special:AbuseLog|details=%(h)d}} details])."\
		% {'f':filter, 'n':name, 'h':hit}
	else:
		reason = "Tripped 10 abuse filters in the last 5 minutes: "\
		"([{{fullurl:Special:AbuseLog|wpSearchUser=%s}} details])."\
		% (urllib.quote(username))
	editsum = "Reporting [[Special:Contributions/%s]]" % (username)
	if u.isIP:
		line = "\n* {{IPvandal|%s}} - " % (username)
	else:
		line = "\n* {{Vandal|%s}} - " % (username)
	line = line.decode('utf8')
	line += reason+" ~~~~"
	try:
		AIV.edit(appendtext=line, summary=editsum)
	except api.APIError: # hacky workaround for mystery error
		time.sleep(1)
		AIV.edit(appendtext=line, summary=editsum)

namecache = timedTracker(expiry=86400)
	
def filterName(filterid):
	filterid = str(filterid)
	if filterid in namecache:
		return namecache[filterid]
	params = {'action':'query', 
		'list':'abusefilters',
		'abfprop':'description',
		'abfstartid':filterid,
		'abflimit':1
	}
	req = api.APIRequest(site, params, False)
	res = req.query(False)
	name = res['query']['abusefilters'][0]['description']
	namecache[filterid] = name
	return name
	
def getLists():
	global immediate, vandalism
	lists = page.Page(site, "User:Mr.Z-bot/filters.js", check=False)
	cont = lists.getWikiText(force=True)
	lines = cont.splitlines()
	for line in lines:
		if line.startswith('#') or not line:
			continue
		if line.startswith('immediate') or line.startswith('vandalism'):
			(type, filters) = line.split('=')
			type = type.strip()
			filters = validateFilterList(filters, type)
			if not filters:
				sendToChannel("Syntax error detected in filter list page - [[User:Mr.Z-bot/filters.js]]")
	vandalism = set([str(f) for f in vandalism])
	immediate = set([str(f) for f in immediate])
			
validate = re.compile('^[0-9, ]*?$')
def validateFilterList(filters, type):
	global immediate, vandalism
	if not validate.match(filters):
		return False
	elif not type in ('immediate', 'vandalism'):
		return False
	else:
		prev = eval(type)
		try:
			exec( type + ' = set([' + filters + '])', locals(), globals())
		except:
			exec( type + ' = ' + repr(prev), locals(), globals())
			return False
		if not isinstance(eval(type), set):
			exec( type + ' = ' + repr(prev), locals(), globals())
			return False
		return True
		
if __name__ == "__main__":
	main()
		
