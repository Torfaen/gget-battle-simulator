"""Port of the reference-array introsort used by NPC counter override loading.

Keeps native equal-key permutations. A stable sort changes the first matching
rule when multiple authored entries share a priority.
"""
from .original import i32


def priority_sort(rows, *, depth_limit=None,compare=None):
    items=list(rows)
    if compare is None:
        def compare(first,second):
            a,b=i32(first['Priority']),i32(second['Priority'])
            return (b>a)-(b<a)
    def swap(first,second):items[first],items[second]=items[second],items[first]
    def swap_greater(first,second):
        if first!=second and compare(items[first],items[second])>0:swap(first,second)
    def insertion(lo,hi):
        for index in range(lo,hi):
            value=items[index+1];position=index
            while position>=lo and compare(value,items[position])<0:
                items[position+1]=items[position];position-=1
            items[position+1]=value
    def down_heap(index,count,lo):
        value=items[lo+index-1]
        while index<=count//2:
            child=2*index
            if child<count and compare(items[lo+child-1],items[lo+child])<0:child+=1
            if compare(value,items[lo+child-1])>=0:break
            items[lo+index-1]=items[lo+child-1];index=child
        items[lo+index-1]=value
    def heap(lo,hi):
        count=hi-lo+1
        for index in range(count//2,0,-1):down_heap(index,count,lo)
        for index in range(count,1,-1):
            swap(lo,lo+index-1);down_heap(1,index-1,lo)
    def partition(lo,hi):
        middle=lo+(hi-lo)//2
        swap_greater(lo,middle);swap_greater(lo,hi);swap_greater(middle,hi)
        pivot=items[middle];swap(middle,hi-1)
        left,right=lo,hi-1
        while left<right:
            left+=1
            while compare(items[left],pivot)<0:left+=1
            right-=1
            while compare(pivot,items[right])<0:right-=1
            if left>=right:break
            swap(left,right)
        swap(left,hi-1)
        return left
    def intro(lo,hi,depth):
        while hi>lo:
            count=hi-lo+1
            if count<=16:
                if count==2:swap_greater(lo,hi)
                elif count==3:
                    swap_greater(lo,hi-1);swap_greater(lo,hi);swap_greater(hi-1,hi)
                else:insertion(lo,hi)
                return
            if depth==0:heap(lo,hi);return
            depth-=1
            pivot=partition(lo,hi)
            intro(pivot+1,hi,depth)
            hi=pivot-1
    if len(items)>1:
        intro(0,len(items)-1,2*len(items).bit_length() if depth_limit is None else depth_limit)
    return items
