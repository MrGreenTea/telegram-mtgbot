gcc -c -fPIC match.c -o match.o -std=c99; gcc -shared -Wl,-soname,match.so -o match.so  match.o
