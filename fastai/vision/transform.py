from ..torch_core import *
from .image import *

_all__ = ['apply_perspective', 'brightness', 'contrast', 'crop', 'crop_pad', 'dihedral', 'flip_lr', 'get_transforms', 
          'jitter', 'pad', 'perspective_warp', 'rand_crop', 'rand_zoom', 'rotate', 'skew', 'squish', 'symmetric_warp', 'tilt', 
          'zoom', 'zoom_crop', 'zoom_squish']

@TfmLighting
def brightness(x, change:uniform):
    "`change` brightness of image `x`"
    return x.add_(scipy.special.logit(change))

@TfmLighting
def contrast(x, scale:log_uniform):
    "`scale` contrast of image `x`"
    return x.mul_(scale)

@TfmAffine
def rotate(degrees:uniform):
    "Affine func that rotates the image"
    angle = degrees * math.pi / 180
    return [[cos(angle), -sin(angle), 0.],
            [sin(angle),  cos(angle), 0.],
            [0.        ,  0.        , 1.]]

def _get_zoom_mat(sw:float, sh:float, c:float, r:float)->AffineMatrix:
    "`sw`,`sh` scale width,height - `c`,`r` focus col,row"
    return [[sw, 0,  c],
            [0, sh,  r],
            [0,  0, 1.]]

@TfmAffine
def zoom(scale:uniform=1.0, row_pct:uniform=0.5, col_pct:uniform=0.5):
    "Zoom image by `scale`. `row_pct`,`col_pct` select focal point of zoom"
    s = 1-1/scale
    col_c = s * (2*col_pct - 1)
    row_c = s * (2*row_pct - 1)
    return _get_zoom_mat(1/scale, 1/scale, col_c, row_c)

@TfmAffine
def squish(scale:uniform=1.0, row_pct:uniform=0.5, col_pct:uniform=0.5):
    "Squish image by `scale`. `row_pct`,`col_pct` select focal point of zoom"
    if scale <= 1:
        col_c = (1-scale) * (2*col_pct - 1)
        return _get_zoom_mat(scale, 1, col_c, 0.)
    else:
        row_c = (1-1/scale) * (2*row_pct - 1)
        return _get_zoom_mat(1, 1/scale, 0., row_c)

@TfmCoord
def jitter(c, img_size, magnitude:uniform):
    return c.add_((torch.rand_like(c)-0.5)*magnitude*2)

@TfmPixel
def flip_lr(x): return x.flip(2)

@TfmPixel
def dihedral(x, k:partial(uniform_int,0,8)):
    "Randomly flip `x` image based on k"
    flips=[]
    if k&1: flips.append(1)
    if k&2: flips.append(2)
    if flips: x = torch.flip(x,flips)
    if k&4: x = x.transpose(1,2)
    return x.contiguous()

@partial(TfmPixel, order=-10)
def pad(x, padding, mode='reflect'):
    "Pad `x` with `padding` pixels. `mode` fills in space ('constant','reflect','replicate')"
    return F.pad(x[None], (padding,)*4, mode=mode)[0]

@TfmPixel
def crop(x, size, row_pct:uniform=0.5, col_pct:uniform=0.5):
    "Crop `x` to `size` pixels. `row_pct`,`col_pct` select focal point of crop"
    size = listify(size,2)
    rows,cols = size
    row = int((x.size(1)-rows+1) * row_pct)
    col = int((x.size(2)-cols+1) * col_pct)
    return x[:, row:row+rows, col:col+cols].contiguous()

@TfmCrop
def crop_pad(x, size, padding_mode='reflect',
             row_pct:uniform = 0.5, col_pct:uniform = 0.5):
    "Crop and pad tfm - `row_pct`,`col_pct` sets focal point"
    if padding_mode=='zeros': padding_mode='constant'
    size = listify(size,2)
    if x.shape[1:] == size: return x
    rows,cols = size
    if x.size(1)<rows or x.size(2)<cols:
        row_pad = max((rows-x.size(1)+1)//2, 0)
        col_pad = max((cols-x.size(2)+1)//2, 0)
        x = F.pad(x[None], (col_pad,col_pad,row_pad,row_pad), mode=padding_mode)[0]
    row = int((x.size(1)-rows+1)*row_pct)
    col = int((x.size(2)-cols+1)*col_pct)

    x = x[:, row:row+rows, col:col+cols]
    return x.contiguous() # without this, get NaN later - don't know why

def rand_zoom(*args, **kwargs):
    "Random zoom tfm"
    return zoom(*args, row_pct=(0,1), col_pct=(0,1), **kwargs)
def rand_crop(*args, **kwargs):
    "Random crop and pad"
    return crop_pad(*args, row_pct=(0,1), col_pct=(0,1), **kwargs)
def zoom_crop(scale:float, do_rand:bool=False, p:float=1.0):
    "Randomly zoom and/or crop"
    zoom_fn = rand_zoom if do_rand else zoom
    crop_fn = rand_crop if do_rand else crop_pad
    return [zoom_fn(scale=scale, p=p), crop_fn()]

def _find_coeffs(orig_pts:Points, targ_pts:Points)->Tensor:
    "Find 8 coeff mentioned [here](https://web.archive.org/web/20150222120106/xenia.media.mit.edu/~cwren/interpolator/)"
    matrix = []
    #The equations we'll need to solve.
    for p1, p2 in zip(targ_pts, orig_pts):
        matrix.append([p1[0], p1[1], 1, 0, 0, 0, -p2[0]*p1[0], -p2[0]*p1[1]])
        matrix.append([0, 0, 0, p1[0], p1[1], 1, -p2[1]*p1[0], -p2[1]*p1[1]])

    A = FloatTensor(matrix)
    B = FloatTensor(orig_pts).view(8)
    #The 8 scalars we seek are solution of AX = B
    return torch.gesv(B,A)[0][:,0]

def _apply_perspective(coords:FlowField, coeffs:Points)->FlowField:
    "Transform `coords` with `coeffs`"
    size = coords.size()
    #compress all the dims expect the last one ang adds ones, coords become N * 3
    coords = coords.view(-1,2)
    #Transform the coeffs in a 3*3 matrix with a 1 at the bottom left
    coeffs = torch.cat([coeffs, FloatTensor([1])]).view(3,3)
    coords = torch.addmm(coeffs[:,2], coords, coeffs[:,:2].t())
    coords.mul_(1/coords[:,2].unsqueeze(1))
    return coords[:,:2].view(size)

_orig_pts = [[-1,-1], [-1,1], [1,-1], [1,1]]

def _perspective_warp(c:FlowField, targ_pts:Points):
    "Apply warp to `targ_pts` from `_orig_pts` to `c` `FlowField`"
    return _apply_perspective(c, _find_coeffs(_orig_pts, targ_pts))

@TfmCoord
def perspective_warp(c, img_size, magnitude:partial(uniform,size=8)=0):
    "Apply warp to `c` and with size `img_size` with `magnitude` amount"

    magnitude = magnitude.view(4,2)
    targ_pts = [[x+m for x,m in zip(xs, ms)] for xs, ms in zip(_orig_pts, magnitude)]
    return _perspective_warp(c, targ_pts)

@TfmCoord
def symmetric_warp(c, img_size, magnitude:partial(uniform,size=4)=0):
    "Apply warp to `c` with size `img_size` and `magnitude` amount"
    m = listify(magnitude, 4)
    targ_pts = [[-1-m[3],-1-m[1]], [-1-m[2],1+m[1]], [1+m[3],-1-m[0]], [1+m[2],1+m[0]]]
    return _perspective_warp(c, targ_pts)

@TfmCoord
def tilt(c, img_size, direction:rand_int, magnitude:uniform=0):
    "Tilt `c` field and resize to`img_size` with random `direction` and `magnitude`"
    orig_pts = [[-1,-1], [-1,1], [1,-1], [1,1]]
    if direction == 0:   targ_pts = [[-1,-1], [-1,1], [1,-1-magnitude], [1,1+magnitude]]
    elif direction == 1: targ_pts = [[-1,-1-magnitude], [-1,1+magnitude], [1,-1], [1,1]]
    elif direction == 2: targ_pts = [[-1,-1], [-1-magnitude,1], [1,-1], [1+magnitude,1]]
    elif direction == 3: targ_pts = [[-1-magnitude,-1], [-1,1], [1+magnitude,-1], [1,1]]
    coeffs = find_coeffs(orig_pts, targ_pts)
    return apply_perspective(c, coeffs)

@TfmCoord
def skew(c, img_size, direction:rand_int, magnitude:uniform=0):
    "Skew `c` field and resize to`img_size` with random `direction` and `magnitude`"
    orig_pts = [[-1,-1], [-1,1], [1,-1], [1,1]]
    if direction == 0:   targ_pts = [[-1-magnitude,-1], [-1,1], [1,-1], [1,1]]
    elif direction == 1: targ_pts = [[-1,-1-magnitude], [-1,1], [1,-1], [1,1]]
    elif direction == 2: targ_pts = [[-1,-1], [-1-magnitude,1], [1,-1], [1,1]]
    elif direction == 3: targ_pts = [[-1,-1], [-1,1+magnitude], [1,-1], [1,1]]
    elif direction == 4: targ_pts = [[-1,-1], [-1,1], [1+magnitude,-1], [1,1]]
    elif direction == 5: targ_pts = [[-1,-1], [-1,1], [1,-1-magnitude], [1,1]]
    elif direction == 6: targ_pts = [[-1,-1], [-1,1], [1,-1], [1+magnitude,1]]
    elif direction == 7: targ_pts = [[-1,-1], [-1,1], [1,-1], [1,1+magnitude]]
    coeffs = find_coeffs(orig_pts, targ_pts)
    return apply_perspective(c, coeffs)

def get_transforms(do_flip:bool=True, flip_vert:bool=False, max_rotate:float=10., max_zoom:float=1.1,
                   max_lighting:float=0.2, max_warp:float=0.2, p_affine:float=0.75,
                   p_lighting:float=0.75, xtra_tfms:float=None)->Collection[Transform]:
    "Utility func to easily create list of `flip`, `rotate`, `zoom`, `warp`, `lighting` transforms"
    res = [rand_crop()]
    if do_flip:    res.append(dihedral() if flip_vert else flip_lr(p=0.5))
    if max_warp:   res.append(symmetric_warp(magnitude=(-max_warp,max_warp), p=p_affine))
    if max_rotate: res.append(rotate(degrees=(-max_rotate,max_rotate), p=p_affine))
    if max_zoom>1: res.append(rand_zoom(scale=(1.,max_zoom), p=p_affine))
    if max_lighting:
        res.append(brightness(change=(0.5*(1-max_lighting), 0.5*(1+max_lighting)), p=p_lighting))
        res.append(contrast(scale=(1-max_lighting, 1/(1-max_lighting)), p=p_lighting))
    #       train                   , valid
    return (res + listify(xtra_tfms), [crop_pad()])

#To keep?
def _compute_zs_mat(sz:TensorImageSize, scale:float, squish:float,
                   invert:bool, row_pct:float, col_pct:float)->AffineMatrix:
    "Utility routine to compute zoom/squish matrix"
    orig_ratio = math.sqrt(sz[2]/sz[1])
    for s,r,i in zip(scale,squish, invert):
        s,r = math.sqrt(s),math.sqrt(r)
        if s * r <= 1 and s / r <= 1: #Test if we are completely inside the picture
            w,h = (s/r, s*r) if i else (s*r,s/r)
            w /= orig_ratio
            h *= orig_ratio
            col_c = (1-w) * (2*col_pct - 1)
            row_c = (1-h) * (2*row_pct - 1)
            return get_zoom_mat(w, h, col_c, row_c)

    #Fallback, hack to emulate a center crop without cropping anything yet.
    if orig_ratio > 1: return get_zoom_mat(1/orig_ratio**2, 1, 0, 0.)
    else:              return get_zoom_mat(1, orig_ratio**2, 0, 0.)

@TfmCoord
def zoom_squish(c, size, scale:uniform=1.0, squish:uniform=1.0, invert:rand_bool=False,
                row_pct:uniform=0.5, col_pct:uniform=0.5):
    #This is intended for scale, squish and invert to be of size 10 (or whatever) so that the transform
    #can try a few zoom/squishes before falling back to center crop (like torchvision.RandomResizedCrop)
    m = _compute_zs_mat(size, scale, squish, invert, row_pct, col_pct)
    return affine_mult(c, FloatTensor(m))
