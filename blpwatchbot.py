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
import settings
import re
site = wiki.Wiki()
site.setMaxlag(-1)
site.login(settings.bot, settings.botpass)

def main():
	listpage = page.Page(site, "Wikipedia:Biographies of living persons/Noticeboard/Watchlist")
	links = set(listpage.getLinks())
	blpn = page.Page(site, "Wikipedia:Biographies of living persons/Noticeboard")
	text = blpn.getWikiText()
	titles = getPossibleTitles(text)
	add = titles.difference(links)
	if add:
		text = ''
		for title in add:
			text += '\n#[['+title+']]'
		comment = "Adding "+str(len(add))+" page"
		if len(add) > 1:
			comment+='s'
		listpage.edit(summary=comment, bot=True, appendtext=text.encode('utf8'))
	
def getPossibleTitles(text):
	secreg = re.compile('\n==(.*?)==\n')
	lareg = re.compile('\{\{la\|(.*?)\}\}', re.I)
	initial = pagelist.listFromTitles(site, lareg.findall(text), followRedir=True)
	titles = set([p.title for p in initial if p.exists and p.namespace < 2])
	sections = secreg.findall(text)
	links = re.compile('\[\[(.*?)\]\]')
	for s in sections:
		s = unicode(s, 'utf8')
		if True in [title in s for title in titles]:
			continue
		s = s.strip()
		ls = links.findall(s)
		if ls:
			pages = pagelist.listFromTitles(site, ls, followRedir=True)
			real = set([p.title for p in pages if p.exists and p.namespace < 2])
			titles.update(real)
			continue
		p = page.Page(site, s)
		if p.exists:
			titles.add(p.title)
			continue
		dabcheck = checkDabs(s)
		if dabcheck:
			titles.add(dabcheck)
			continue
		last = lastChecks(s)
		if last:
			titles.update(last)
			continue
	return titles
		
def checkDabs(s):
	max = 3
	while True:
		dab = re.compile('((?:\S*? ){2,'+str(max)+'}\(.*?\))')
		poss = dab.search(s)
		if poss:
			t = poss.group(1)
			p = page.Page(site, t)
			if p.exists and p.namespace < 2:
				return p.title
		if max < 5:
			max += 1
		else:
			return False
			
def lastChecks(s):
	punct = re.compile('[:\-,\–\—"\']')
	test = punct.split(s)
	if len(test) > 1:
		for t in test:
			if t.strip():
				p = page.Page(site, t.strip())
				if p.exists and p.namespace < 2 and len(p.title.split(' ')) > 1:
					return set([p.title])
	words = s.split(' ')
	titles = []
	if len(words) > 2:
		for x in range(2, len(words)):
			for i in range(0, len(words)-x+1):
				titles.append(' '.join(words[i:x+i]))
		pages = pagelist.listFromTitles(site, titles, followRedir=True)
		real = set([p.title for p in pages if p.exists and p.namespace < 2])
		return real
	return False
			
if __name__ == '__main__':
	main()
