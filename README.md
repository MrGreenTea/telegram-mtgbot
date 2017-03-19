1. clone https://github.com/jhawthorn/fzy/
2. rename config.def.h to config.h
3. change ../config.h includes to config.h
4. run gcc -c -fPIC match.c -o match.o -std=c99; gcc -shared -Wl,-soname,match.so -o match.so  match.o
