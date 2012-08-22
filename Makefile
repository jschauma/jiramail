# A simple Makefile to (un)install jiramail.

PREFIX=/usr/local

all:
	@echo "Available targets:"
	@echo " install"
	@echo " uninstall"

install:
	mkdir -p ${PREFIX}/bin
	mkdir -p ${PREFIX}/share/man/man1
	install -c -m 755 src/jiramail.py ${PREFIX}/bin/jiramail
	install -c -m 444 doc/jiramail.1 ${PREFIX}/share/man/man1/jiramail.1

uninstall:
	rm -f ${PREFIX}/bin/jiramail ${PREFIX}/share/man/man1/jiramail.1
