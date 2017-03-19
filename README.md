1. clone https://github.com/jhawthorn/fzy/tree/3f70bef1df0c5b85c94e9d4ffea95b51e813160f
2. rename config.def.h to config.h
3. change ../config.h includes to config.h
4. run gcc -c -fPIC match.c -o match.o -std=c99; gcc -shared -Wl,-soname,match.so -o match.so  match.o
