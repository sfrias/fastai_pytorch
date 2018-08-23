
        #################################################
        ### THIS FILE WAS AUTOGENERATED! DO NOT EDIT! ###
        #################################################

def get_resize_target(img_sz, crop_target, do_crop=False):
    if crop_target is None: return None
    ch,r,c = img_sz
    target_r,target_c = crop_target
    ratio = (min if do_crop else max)(r/target_r, c/target_c)
    return ch,round(r/ratio),round(c/ratio)