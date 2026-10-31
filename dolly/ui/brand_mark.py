"""Dolly's mark beside the sidebar wordmark (USER-ADDITIONS UPDATE 21: Kara, "add dolly logo near
deadlock dolly in sidebar").

The PNGs below are assets/dolly.png (256 x 256, black mark on white) scaled down with a high-quality
filter to 22, 33 and 44 px (the 1x / 1.5x / 2x art buckets), with the ink turned white and the white
ground transparent, so the mark reads on the dark sidebar like the wordmark next to it. They live in
code rather than assets/ so every build ships them.
"""
from __future__ import annotations

import tkinter as tk

from .scale import bucket

SIZE = 22   # design px: about the wordmark's cap height plus its descender room

PNGS = {
    "1": (
        "iVBORw0KGgoAAAANSUhEUgAAABYAAAAWCAYAAADEtGw7AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAA"
        "DsMAAA7DAcdvqGQAAAHASURBVEhLrdPLy05RFMfx10tiIn8CZSQKAwYmFBlKRkZSBhgSkuvQxDW3lHtIyfsql5RLJgZmBiRm"
        "ppgL9dF6W5v9HOc5z1FWrc7e+/zWd6+99tpjmJY+3uFF01c7PpaT+I7ytVjSst7mA+BmVsXj3wYcwVGs7KHvDd6LLTiIHT30"
        "I8HlaE/9sefVelP/T+DlCTyECzleOiJmClwmbYKS7XfMx7IE3x0SMwAelnGsrUrQYxzAfrwckXUruGRZ/FECr+MMruFEbjDR"
        "EtcKDp9djdfjHjbhEy7iCd5hIx5gdaWfVY0HwCvwEdtzfgO38QOLsTmB67IUl3EztbvxoXpAA+CTGVCCXmEb1mABzuEs5mZP"
        "x7/3uFTFHW4DT8etSvQWp/IU87ALW7P9vmb7fan0p4eVovizShwXFZlOYieOZaudz5oXu9pg/AWegysV/HMe+03CX+e/uIef"
        "qbmf2c7s6opFWJjjCAiLy4vN9mAf7uR6WJwotFGeKFdrxs0+jpo/rCDHswua0NpbM257QTPykYR9q44fm9SwZlwnuIbHky7W"
        "Be0NruEvshO6oAPgMvmf/jvjgMe3y0tGzfWmT2l+AXJeELJLUas2AAAAAElFTkSuQmCC"
    ),
    "1.5": (
        "iVBORw0KGgoAAAANSUhEUgAAACEAAAAhCAYAAABX5MJvAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAA"
        "DsMAAA7DAcdvqGQAAAMYSURBVFhHxZZZqI9BGMZtKbsoLpTIdmcNKYmSEhFCkT1LWUoJRbIksmTNUraiUCglQrjgiguhLCVb"
        "oRBCyPbTc3pGc77z/U//b9Q5T719883yvM/MvPPO1AHqAXX9rWmr8FvHP/rWllUSIVVFTGNGAauAzv/BkSxC/ScAd4BdwF2g"
        "dyJPkoiwjNeAwS6vBvYW5AlcSSLC4GPAWQu5DexI5EkSob7NgZdUxlugZQJXsggFo/AbOB4JWZPAVVhE6P/GTk+67p7/n/u/"
        "EF+KiFl2+Blo5bo+rhPGF+QrJEJ9mgDv7OwWsBjYCiwEnrn+MVC/AGdhEUvs6A9wFFgBLAXWAafdJkwtwFm2CLU3A74D54Fh"
        "wAJgv7PmAW/DIOCGY6ZBmbwlReg/rlN5M/DA5Yue8U9gXrQCa93+CViUw5HnJ1eEym2iOn11/r8A04BDwGjgqUX1AH4Bl4Dp"
        "wD5gNvARaBg5z/KWFKGvlvU9MDkarOC7AizzjK8CrYFOQH+gI9DBKyBorAJVgRs45pp3RMZflat8ZrS0gvZenTXTGZ7tI2Cs"
        "s+YZi9sNNHWQKh62AZssSrwSFUMrGXxWETEm01lbcM5lzWCKZ6++ukUlSuX7QBeXhwIjvQqC7pcYX4EhpUSE5RmeGRSg2ejK"
        "1kwVjI2BU8BlX146PTsdrAeB5VkCr0zf6rYjDpZxzgUxtvvKFk44Dnr6WOot0RV45XatkuImhuIhFlCtiNAY0nOAllFBuxGY"
        "5K16CPRz+01v1xYHYRYDSvipVsQRH0MFZcAToL23QPgGzI/alSck6kdUp9XRFipo8/zkilB5IHAdaOvZx3jtujk+BQrYPcBE"
        "3yG63gOUK7o5l4hPxzjrK1eETCIauV6mJJTFBb8fNgDrvR0xJCC8O2XtgO5FRISBcTmbQ4TDzqJ6b8aIBeRxliUia4FA6TiG"
        "YmSlL7aADzkCSlkhEbEQxUIpSECvMvkCZyERYZAs7wgqDyhvlMsV+AqLCAOzQlIEBK4kEWGwTG+GF4kCAk+yiEAga5E4PnBU"
        "uUVrw/6JkCp9a9oq/P4Fw8alzZc2pEYAAAAASUVORK5CYII="
    ),
    "2": (
        "iVBORw0KGgoAAAANSUhEUgAAACwAAAAsCAYAAAAehFoBAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAA"
        "DsMAAA7DAcdvqGQAAASRSURBVFhH1ZhpyG9TFIfvvabIPGceM88Zogwp5Ip7QxQpHwylyAcfTEXKEOKDIZmFhCQZMyRKMoaQ"
        "zJQ5c2YePW9ra9nv+Q/v/+xb7/3V6py9zz5r/c46a6+1zpkDzAPmxnE2ixznzomBx8VB/kd46gkayDQjjWSREFbPfsBdwLnA"
        "6o11z2tJWB0HAr8DjwJvAB8A6zXU34yw9y8JfAacncLhOeCBBvqLjaaElwG+ARYkwjcAj6dxfd9MpDlh5QXgK+AI4ETg79lM"
        "eLcg2IW9G9loQrh477FE8BfgrzR+uoGXmxLWgxnG8YXVnOmur51mhG9JxF6NuVWBH9P8lQ3s9C4c3rcR8F0itm96/aen+S+A"
        "tXvaakL43kTq9phbEVg+zl9O16/uaasXYe/ZMZERbwLvA9+G1x1/mK7/Aazfw97EMex65f5Exip3OTAf2APYCzg2ikcOmWsm"
        "sFdsTuxh1+8SBExhZwDrAgcAV8TxlGiAtgNWAs6PPP1PxP0kNifycPHuk8CfYVxvvpW8eDJwXxrbT6wC7B7jm2dos9gdSrgQ"
        "65q3qgm96AY7PpETh8YGK/g5HmoJ4KqY22SI/kHzA0Oi3LRZda3MPwt8AmwMfB2x6kP8FmQMg5LSXou++I7IGKvF/J0DdBeb"
        "XZw6PVwWXxQEDknXPbqhxFHAJXEuDge2BNZJOraNAnJdWrcQuDTOs0M8qtN++rwO0gMJLwVclgzYE+yfFDwDfB+bTOXC8TFx"
        "3bL8HvASsHXMXZv0+Xa2ifOSt5Uj0xpxzjge9mhc1tDT28erFg9GOGjctLVB3LtrbMQTwou2mivHtYNi81lofAs/hC6rn1ml"
        "C6W3Hkp4TeCJ+k7gS+D1OPebbQ3g4FhfvHQS8HEam8YMizLeOfK0YeIeEK8AvyY7Bca46TBz69x0ni8b3huEh1OmMCzOAlYA"
        "1gI+Bx6Jh7OtNMSM1YdivUVkw6qY1HCDlofMvDoJl4sSMA674KeQJdYMUVA2il6xmhmDxei7aZ1kdkjjGiV7dHGaFhL1Al+d"
        "r6wL7vbT4vypiFWrnNXNey0oN8bOt+cwtoXnOUdndHl2bMJlkTFq+a1h77B5FAKzQa5sewLXp7Gb0CxyAbBPms8whAaRnRHh"
        "o0OhHViNF2NNToPiMOC2as6YN5vkpr7g0zhuNYLLUMLOLRfe9XvNV/x2ZUj4pWwmODW1kqZGew3xfGQT01pJZRm+Cd+iWah8"
        "+9VcxiKsnBktoz9JXGOxeKe2GPn2OGDTCAdzqxtrJ2CL6NS6cGsKA0v23fFgXXxGEjYdWYaLwnKTv57yrs+QuCnvnhA9ZtPe"
        "Bb8Da91LR9tacxmLcFlUX3NsSrP8dsHMcHFkixIWNWqyw+zNiPAgcb3J3599NYxzW06zwU/1xSFkR0kvwkWBG/GjmlE0Q12Y"
        "lGyxN7DSjSveZyNu/zAK5Sujj61eHs6KRpG+qSfZYqcJ4aLMlNZF2k3Yl2yx0YxwUVh7uhXZor8pYaWQts8Y1HVNKlN6WhNW"
        "1GWp9Qu5td5FQlhp6dki00JicZD/POwTeJzNMuXpfwH3GD+ki6reoAAAAABJRU5ErkJggg=="
    ),
}


def photo(master, scale: float) -> tk.PhotoImage:
    """The light mark pre-scaled for ``scale``'s art bucket (decoded by Tk)."""
    return tk.PhotoImage(master=master, data=PNGS.get(bucket(scale), PNGS["1"]))
