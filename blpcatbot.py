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
from wikitools import *
import MySQLdb
import datetime
import re
import os
import settings

unref = ['Unreferenced',
'Notverifiable',
'Unsourced',
'Unverified',
'Unref',
'References',
'Uncited-article',
'Unrefart',
'Citesources',
'NR',
'No references',
'Unreferencedart',
'Unrefarticle',
'Unreferenced article',
'Unreferenced art',
'Noref',
'Norefs',
'Noreferences',
'Unrefreenced',
'Cleanup-cite',
'References needed',
'Nr',
'No refs',
'UnreferencedArticle',
'UnreferenceArticle',
'No ref']

blpunref = ['BLP unsourced',
'UnsourcedBLP',
'BLPunreferenced',
'Unreferencedblp',
'Blpunsourced',
'Unsourcedblp', 
'BLPunsourced',
'BLPUnreferenced',
'Unsourced BLP'
]

unrefs = '|'.join(unref)
blpunrefs = '|'.join(blpunref)
articleissues = re.compile('\{\{(Article ?issues|Ai|issues)(?P<content>[^\}]*)\}\}', re.I)
ai2 = re.compile('([^=\n\|\s]*)\s*=\s*([^\|\n]*)', re.I)
urt = re.compile('\{\{('+unrefs+')(?:\|\s*(?:date\s*=\s*)?(?P<date>[^\}]*))?\}\}\n?', re.I)
blpurt = re.compile('\{\{('+blpunrefs+')(?:\|\s*(?:date\s*=\s*)?(?P<date>[^\}]*))?\}\}', re.I)
primary = re.compile('\{\{(primarysources|citecheck)(?:\|\s*(?:date\s*=\s*)?(?P<date>[^\}]*))?\}\}', re.I)

rmdate = re.compile('date\s*=\s*', re.I)

site = wiki.Wiki()
site.login(settings.bot, settings.botpass)

query = """
SELECT DISTINCT page_title FROM page
JOIN categorylinks AS clA ON page_id=clA.cl_from
JOIN categorylinks AS clB ON page_id=clB.cl_from AND clA.cl_from=clB.cl_from
WHERE clA.cl_to="Living_people" AND clB.cl_to="All_articles_lacking_sources" AND page_namespace=0
ORDER BY page_title ASC"""

def main():
	db = MySQLdb.connect(host="enwiki.labsdb", db='enwiki_p', read_default_file="/data/project/zbot/replica.my.cnf")
	db.autocommit(True)
	cursor = db.cursor()
	cursor.execute(query)
	while True:
		title = cursor.fetchone()
		if not title:
			break
		p = page.Page(site, title[0].decode('utf8'))
		text = p.getWikiText()
		# Get article issues template
		ai = articleissues.search(text)
		aiinner = None
		if ai:
			aiinner = dict(ai2.findall(ai.group('content')))
			if 'unref' in aiinner:
				aiinner['unreferenced'] = aiinner['unref']
			if not 'unreferenced' in aiinner:
				ai = None
			if 'section' in aiinner:
				ai = None
		# Get the unrefernced template
		unreftemp = urt.search(text)
		if unreftemp and unreftemp.group(0).count('|') > 1  and 'date' in unreftemp.groupdict() and unreftemp.group('date') and 'section' in unreftemp.group('date'):
			unreftemp = None
		if len(urt.findall(text)) > 1:
			continue
		if len(articleissues.findall(text)) > 1:
			continue
		# Look for a BLP unsourced template
		blpunreftemp = blpurt.search(text)
		# Get the date from one of the templates
		timestamp = False
		if unreftemp and unreftemp.group('date') and 'date' in unreftemp.groupdict():
			d = unreftemp.group('date')
			if isValidTime(d):
				timestamp = d.strip()
			elif len(d.split('|')) > 1:
				for s in d.split('|'):
					d2 = rmdate.sub('', s)
					if isValidTime(d2):
						timestamp = d2.strip()
						break
		if ai and not timestamp and 'unreferenced' in aiinner:
			if isValidTime(aiinner['unreferenced']):
				timestamp = aiinner['unreferenced'].strip()
		# Remove or add templates as necessary
		newtext = ''
		timestamp = datetime.datetime.utcnow().strftime('%B %Y')
		if blpunreftemp and ai and unreftemp:               # All 3
			newtext = removeFromAI(aiinner, text)
			newtext = removeUnref(newtext)
		elif blpunreftemp and not ai and unreftemp:         # BLPur and unreferenced
			newtext = removeUnref(text)
		elif blpunreftemp and ai and not unreftemp:         # BLPur and AI
			newtext = removeFromAI(aiinner, text)
		elif blpunreftemp and not ai and not unreftemp:     # Only BLPur
			continue
		elif not blpunreftemp and ai and unreftemp:         # AI and unreferenced
			newtext = removeFromAI(aiinner, text)
			newtext = removeUnref(newtext)
			newtext = addtoAI(newtext, timestamp, aiinner)
		elif not blpunreftemp and not ai and unreftemp:     # Only unreferenced
			newtext = replaceUnref(text, timestamp)
		elif not blpunreftemp and ai and not unreftemp:     # Only AI
			newtext = removeFromAI(aiinner, text)
			newtext = addtoAI(newtext, timestamp, aiinner)
		elif not blpunreftemp and not ai and not unreftemp: # Nothing
			continue
		if text == newtext:
			continue
		try:
			p.edit(text=newtext, summary="updating tags: unreferenced [[WP:BLP|BLP]]", minor=True, bot=True)
		except api.APIError, e:
			continue
			
def isValidTime(timestamp):
	try:
		datetime.datetime.strptime(timestamp.strip(), "%B %Y")
		return True
	except:
		return False
	
def removeFromAI(aiinner, text):
	del aiinner['unreferenced']
	if 'unref' in aiinner:
		del aiinner['unref']
	newai = "{{article issues"
	for issue in aiinner.keys():
		newai += "\n| "+issue+ " = "+aiinner[issue]
	newai += "\n}}"
	text = articleissues.sub(newai, text)
	return text

def removeUnref(text):
	return urt.sub('', text)
	
def replaceUnref(text, timestamp):
	if not timestamp:
		timestamp = datetime.datetime.now().strftime('%B %Y')
	template = "{{BLP unsourced|date=%s}}\n" % timestamp
	return urt.sub(template, text)
		
def addtoAI(text, timestamp, aiinner):
	if not timestamp:
		timestamp = datetime.datetime.now().strftime('%B %Y')
	aiinner['BLPunsourced'] = timestamp
	newai = "{{article issues"
	for issue in aiinner.keys():
		newai += "\n| "+issue+ " = "+aiinner[issue]
	newai += "\n}}"
	text = articleissues.sub(newai, text)
	return text

if __name__ == "__main__":
	main()
