# -*- coding: utf-8 -*-
#===========================================================================
#
# Fixpoint library for converting numpy scalars and arrays to quantized
# numpy values
#
# (c) 2015 - 2017 Christian Münker
#===========================================================================
from __future__ import division, print_function, unicode_literals

import re
import six
import logging
logger = logging.getLogger(__name__)

import numpy as np
from pyfda.pyfda_qt_lib import qstr
import pyfda.filterbroker as fb

# TODO: Python2: frmt2float yields zero for all non-floats!
# TODO: Entering the maximum allowed value displays an overflow?!
# TODO: Entering a negative sign with a negative 2sComp. hex or bin number yields 
#       the negative minimum (but no overflow) instead of the expected positive number
# TODO: Overflows in wrap mode are not flagged
# TODO: Entering values outside the FP range as non-float doesn't
#       flag an overflow / yields incorrect results

# TODO: Hex frmt2float resets the first '1'?
# TODO: Hex float2frmt always has a wraparound behaviour

# TODO: Max. value in CSD normalized frac format is 0.+0+0+ ... instead of 0.+00000-
# TODO: Vecorization for hex / csd functions


__version__ = 0.5

def bin2hex(bin_str):
    """
    Convert number `bin_str` in binary format to hex formatted string.
    When `frac=False` (default), `bin_str` is prepended with zeros until 
    the number of bits is a multiple of 4. For a fractional part (`frac = True`),
    zeros are appended.
    """

    wmap ={'0000': '0',
           '0001': '1',
           '0010': '2',
           '0011': '3',
           '0100': '4',
           '0101': '5',
           '0110': '6',
           '0111': '7',
           '1000': '8',
           '1001': '9',
           '1010': 'A',
           '1011': 'B',
           '1100': 'C',
           '1101': 'D',
           '1110': 'E',
           '1111': 'F'}

    i = 0
    hex_str = ""

    # prepend zeros to bin_str until the length is a multiple of 4 bits
    while (len(bin_str) % 4 != 0):
        bin_str = "0" + bin_str

    while (i < len(bin_str)): # map 4 binary bits to one hex digit
        hex_str = hex_str + wmap[bin_str[i:i + 4]]
        i = i + 4

    hex_str = hex_str.strip("0")
    hex_str = "0" if len(hex_str) == 0 else hex_str

    return hex_str


def dec2hex(val, nbits):
    """
    Return `val` in hex format with a wordlength of `nbits` in two's complement
    format. The built-in hex function returns args < 0 as negative values.

    Parameters
    ----------
    val: integer
            The value to be converted in decimal integer format.

    nbits: integer
            The wordlength

    Returns
    -------
    A string in two's complement hex format
    """
    return "{0:X}".format(np.int64((val + (1 << nbits)) % (1 << nbits)))

#------------------------------------------------------------------------------

def dec2csd(dec_val, WF=0):
    """
    Convert the argument `dec_val` to a string in CSD Format.

    Parameters:
    -----------

    dec_val : scalar (integer or real)
              decimal value to be converted to CSD format

    WF: integer
        number of fractional places. Default is WF = 0 (integer number)

    Returns:
    --------
    A string with the CSD value

    Original author: Harnesser
    https://sourceforge.net/projects/pycsd/
    License: GPL2

    """

    logger.debug("Converting {0:f}:".format(dec_val))

    # figure out binary range, special case for 0
    if dec_val == 0 :
        return '0'
    if np.fabs(dec_val) < 1.0 :
        k = 0
    else:
        k = int(np.ceil(np.log2(np.abs(dec_val) * 1.5)))

    logger.debug("to {0:d}.{1:d} format".format(k, WF))

    # Initialize CSD calculation
    csd_digits = []
    remainder = dec_val
    prev_non_zero = False
    k -= 1 # current exponent in the CSD string under construction

    while( k >= -WF): # has the last fractional digit been reached

        limit = pow(2.0, k+1) / 3.0

        logger.debug("\t{0} - {1}".format(remainder, limit))

        # decimal point?
        if k == -1 :
            csd_digits.extend( ['.'] )

        # convert the number
        if prev_non_zero:
            csd_digits.extend( ['0'] )
            prev_non_zero = False

        elif remainder > limit :
            csd_digits.extend( ['+'] )
            remainder -= pow(2.0, k )
            prev_non_zero = True

        elif remainder < -limit :
            csd_digits.extend( ['-'] )
            remainder += pow(2.0, k )
            prev_non_zero = True

        else :
            csd_digits.extend( ['0'] )
            prev_non_zero = False

        k -= 1

        logger.debug(csd_digits)

    # Always have something before the point
    if np.fabs(dec_val) < 1.0:
        csd_digits.insert(0, '0')

    csd_str = "".join(csd_digits)

    return csd_str


def csd2dec(csd_str, int_places=0):
    """
    Convert the CSD string `csd_str` to a decimal, `csd_str` may contain '+' or
    '-', indicating whether the current bit is meant to positive or negative.
    All other characters are simply ignored.

    `csd_str` may be an integer or fractional CSD number.

    Parameters:
    -----------

    csd_str : string

     A string with the CSD value to be converted, consisting of '+', '-', '.'
     and '0' characters.

    Returns:
    --------
    Real value of the CSD string

    Examples:
    ---------

    +00- = +2³ - 2⁰ = +7

    -0+0 = -2³ + 2¹ = -6

    +0.-0- = 2¹ - 1/2¹ - 1/2³ = 1.375

    """
    logger.debug("Converting: {0}".format(csd_str))

    #  Find out what the MSB power of two should be, keeping in
    #  mind we may have a fractional CSD number:
    try:
        (int_str, _) = csd_str.split('.') # split into integer and fractional bits
        csd_str = csd_str.replace('.','') # join integer and fractional bits to one csd string
    except ValueError: # no fractional part
        int_str = csd_str
        _ = ""

    # Intialize calculation, start with the MSB (integer)
    msb_power = len(int_str)-1 #
    dec_val = 0.0

    illegal_chars = re.sub('[+-0 ]', '', csd_str) # test for illegal characters
    if illegal_chars:
        logger.warn("Invalid character(s) {0} for CSD string!".format(illegal_chars))
        return None

    # start from the MSB and work all the way down to the last digit
    for ii in range( len(csd_str) ):

        power_of_two = 2.0**(msb_power-ii)

        if csd_str[ii] == '+' :
            dec_val += power_of_two
        elif csd_str[ii] == '-' :
            dec_val -= power_of_two
        # else
        #    ... all other values are ignored

        logger.debug('  "{0:s}" ({1:d}.{2:d}); 2**{3:d} = {4}; Num={5:f}'.format(
                csd_str[ii], len(int_str), len(_), msb_power-ii, power_of_two, dec_val))

    return dec_val

#------------------------------------------------------------------------
class Fixed(object):
    """
    Implement binary quantization of signed scalar or array-like objects
    in the form yq = WI.WF where WI and WF are the wordlength of integer resp.
    fractional part; total wordlength is W = WI + WF + 1 due to the sign bit.

    q_obj = {'WI':1, 'WF':14, 'ovfl':'sat', 'quant':'round'} or

    q_obj = {'Q':'1.14', 'ovfl':'sat', 'quant':'round'}

    myQ = Fixed(q_obj) # instantiate fixed-point object


    Parameters
    ----------
    q_obj : dict
        defining quantization operation with the keys

    * **'WI'** : integer word length, default: 0

    * **'WF'** : fractional word length; default: 15; WI + WF + 1 = W (1 sign bit)

    * **'Q'**  : Quantization format as string, e.g. '0.15', it is translated 
                 to`WI` and `WF` and deleted afterwards. When both `Q` and `WI` / `WF`
                 are given, `Q` takes precedence

    * **'quant'** : Quantization method, optional; default = 'floor'

      - 'floor': (default) largest integer `I` such that :math:`I \\le x` (= binary truncation)
      - 'round': (binary) rounding
      - 'fix': round to nearest integer towards zero ('Betragsschneiden')
      - 'ceil': smallest integer `I`, such that :math:`I \\ge x`
      - 'rint': round towards nearest int
      - 'none': no quantization

    * **'ovfl'** : Overflow method, optional; default = 'wrap'

      - 'wrap': do a two's complement wrap-around
      - 'sat' : saturate at minimum / maximum value
      - 'none': no overflow; the integer word length is ignored
      
    Additionally, the following keys define the base / display format for the 
    fixpoint number:

    * **'frmt'** : Output format, optional; default = 'float'

      - 'float' : (default)
      - 'int'  : decimal integer, scaled by :math:`2^{WF}`
      - 'bin'  : binary string, scaled by :math:`2^{WF}`
      - 'hex'  : hex string, scaled by :math:`2^{WF}`
      - 'csd'  : canonically signed digit string, scaled by :math:`2^{WF}`
          
    * **'scale'** : Float, the factor between the fixpoint integer representation
                    and its floating point value. 

                    

    Instance Attributes
    -------------------
    q_obj : dict
        A copy of the quantization dictionary (see above)

    quant : string
        Quantization behaviour ('floor', 'round', ...)

    ovfl  : string
        Overflow behaviour ('wrap', 'sat', ...)

    frmt : string
        target output format ('float', 'dec', 'bin', 'hex', 'csd')
        
    point : boolean
        If True, use position of radix point for format conversion
        
    scale : float
        The factor between integer fixpoint representation and the floating point
        value.

    ovr_flag : integer or integer array (same shape as input argument)
        overflow flag   0 : no overflow

                        +1: positive overflow

                        -1: negative overflow

        has occured during last fixpoint conversion

    N_over : integer
        total number of overflows

    N_over_neg : integer
        number of negative overflows

    N_over_pos : integer
        number of positive overflows

    LSB : float
        value of LSB (smallest quantization step)

    MSB : float
        value of most significant bit (MSB)

    digits : integer (read only)
        number of digits required for selected number format and wordlength

    Notes
    -----
    class Fixed() can be used like Matlabs quantizer object / function from the
    fixpoint toolbox, see (Matlab) 'help round' and 'help quantizer/round' e.g.

    q_dsp = quantizer('fixed', 'round', [16 15], 'wrap'); % Matlab

    q_dsp = {'Q':'0.15', 'quant':'round', 'ovfl':'wrap'} # Python

    """

    def __init__(self, q_obj):
        """
        Initialize fixed object with dict q_obj
        """
        # test if all passed keys of quantizer object are known
        self.setQobj(q_obj)
        self.resetN() # initialize overflow-counter
        # arguments for regex replacement with illegal characters
        # ^ means "not", | means "or" and \ escapes
        self.FRMT_REGEX = {
                'bin' : r'[^0|1|.|,|\-]',
                'csd' : r'[^0|\+|\-|.|,]',
                'dec' : r'[^0-9|.|,|\-]',
                'hex' : r'[^0-9A-Fa-f|.|,|\-]'
                        }
#        self.frmt_scale = {'bin' : 2,
#              'csd' : 2,
#              'dec' : 1,
#              'hex' : 16}


    def setQobj(self, q_obj):
        """
        Analyze quantization dict, complete and transform it if needed and
        store it as instance attribute
        """
        for key in q_obj.keys():
            if key not in ['Q','WF','WI','quant','ovfl','frmt','scale']:
                raise Exception(u'Unknown Key "%s"!'%(key))

        q_obj_default = {'WI':0, 'WF':15, 'quant':'round', 'ovfl':'sat', 
                         'frmt':'float', 'scale':1}

        # missing key-value pairs are either taken from default dict or from 
        # class attributes
        for k in q_obj_default.keys():
            if k not in q_obj.keys():
                if not hasattr(self, k):
                    q_obj[k] = q_obj_default[k]
                else:
                    q_obj[k] = getattr(self, k)

        # set default values for parameters if undefined:
        if 'Q' in q_obj:
            Q_str = str(q_obj['Q']).split('.',1)  # split 'Q':'1.4'
            q_obj['WI'] = int(Q_str[0])
            q_obj['WF'] = abs(int(Q_str[1]))
            # remove 'Q' to avoid ambiguities in case 'WI' / 'WF' are set directly
            del q_obj['Q']

        # store parameters as class attributes            
        self.WI    = int(q_obj['WI'])
        self.WF    = int(abs(q_obj['WF']))
        self.W     = self.WF + self.WI + 1            
        self.quant = str(q_obj['quant']).lower()
        self.ovfl  = str(q_obj['ovfl']).lower()
        self.frmt  = str(q_obj['frmt']).lower()
        self.scale = np.float64(q_obj['scale'])

        self.q_obj = q_obj # store quant. dict in instance

        self.LSB = 2. ** -self.WF  # value of LSB 
        self.MSB = 2. ** (self.WI - 1)   # value of MSB 

        self.MAX =  2. * self.MSB - self.LSB
        self.MIN = -2. * self.MSB

        # Calculate required number of places for different bases from total 
        # number of bits:
        if self.frmt == 'dec':
            self.places = int(np.ceil(np.log10(self.W) * np.log10(2.))) + 1
            self.base = 10
        elif self.frmt == 'bin':
            self.places = self.W + 1
            self.base = 2
        elif self.frmt == 'csd':
            self.places = self.W + 1
            self.base = 2
        elif self.frmt == 'hex':
            self.places = int(np.ceil(self.W / 4.)) + 1
            self.base = 16
        elif self.frmt == 'float':
            self.places = 4
            self.base = 0
        else:
            raise Exception(u'Unknown format "%s"!'%(self.frmt))

        self.ovr_flag = 0

#------------------------------------------------------------------------------
    def fixp(self, y, scaling='mult'):
        """
        Return fixed-point integer or fractional representation for `y` 
        (scalar or array-like) with the same shape as `y`.

        Saturation / two's complement wrapping happens outside the range +/- MSB,  
        requantization (round, floor, fix, ...) is applied on the ratio `y / LSB`.

        Parameters
        ----------
        y: scalar or array-like object
            in floating point format to be quantized

        scaling: String
            When `scaling='mult'` (default), `y` is multiplied by `self.scale` before 
            requantizing and saturating, when `scaling='div'`, 
            `y` is divided by `self.scale`.

        Returns
        -------
        float scalar or ndarray
            with the same shape as `y`, in the range 
            `-2*self.MSB` ... `2*self.MSB-self.LSB`

        Examples:
        ---------

        >>> q_obj_a = {'WI':1, 'WF':6, 'ovfl':'sat', 'quant':'round'}
        >>> myQa = Fixed(q_obj_a) # instantiate fixed-point object myQa
        >>> myQa.resetN()  # reset overflow counter
        >>> a = np.arange(0,5, 0.05) # create input signal

        >>> aq = myQa.fixed(a) # quantize input signal
        >>> plt.plot(a, aq) # plot quantized vs. original signal
        >>> print(myQa.N_over, "overflows!") # print number of overflows

        >>> # Convert output to same format as input:
        >>> b = np.arange(200, dtype = np.int16)
        >>> btype = np.result_type(b)
        >>> # MSB = 2**7, LSB = 2**2:
        >>> q_obj_b = {'WI':7, 'WF':-2, 'ovfl':'wrap', 'quant':'round'}
        >>> myQb = Fixed(q_obj_b) # instantiate fixed-point object myQb
        >>> bq = myQb.fixed(b)
        >>> bq = bq.astype(btype) # restore original variable type
        """

        if np.shape(y):
            # create empty arrays for result and overflows with same shape as y
            # for speedup, test for invalid types
            SCALAR = False
            y = np.asarray(y) # convert lists / tuples / ... to numpy arrays
            yq = np.zeros(y.shape)
            over_pos = over_neg = np.zeros(y.shape, dtype = bool)
            self.ovr_flag = np.zeros(y.shape, dtype = int)

            if np.issubdtype(y.dtype, np.number):
                pass
            elif y.dtype.kind in {'U', 'S'}: # string or unicode
                try:
                    y = y.astype(np.float64) # try to convert to float
                except (TypeError, ValueError):
                    try:
                        np.char.replace(y, ' ', '') # remove all whitespace
                        y = y.astype(complex) # try to convert to complex
                    except (TypeError, ValueError) as e: # try converting elements individually
                        y = list(map(lambda y_scalar: 
                            self.fixp(y_scalar, scaling=scaling), y))

            else:
                logger.error("Argument '{0}' is of type '{1}',\n"
                             "cannot convert to float.".format(y, y.dtype))
                y = np.zeros(y.shape)
        else:
            SCALAR = True
            # get rid of errors that have occurred upstream
            if y is None or str(y) == "":
                y = 0
            # If y is not a number, convert to string, remove whitespace and convert
            # to complex format:
            elif not np.issubdtype(type(y), np.number):
                y = qstr(y)
                y = y.replace(' ','') # remove all whitespace
                try:
                    y = float(y)
                except (TypeError, ValueError):
                    try:
                        y = complex(y)
                    except (TypeError, ValueError) as e:
                        logger.error("Argument '{0}' yields \n {1}".format(y,e))
                        y = 0.0
            over_pos = over_neg = yq = 0
            self.ovr_flag = 0

        # convert pseudo-complex (imag = 0) and complex values to real
        y = np.real_if_close(y)
        if np.iscomplexobj(y):
            logger.warning("Casting complex values to real before quantization!")
            # quantizing complex objects is not supported yet
            y = y.real

        y_in = y # y before scaling
        # convert to "fixpoint integer" for requantizing in relation to LSB
        y = y / self.LSB 
        if scaling == 'mult':
            y = y * self.scale

        if   self.quant == 'floor':  yq = np.floor(y)
             # largest integer i, such that i <= x (= binary truncation)
        elif self.quant == 'round':  yq = np.round(y)
             # rounding, also = binary rounding
        elif self.quant == 'fix':    yq = np.fix(y)
             # round to nearest integer towards zero ("Betragsschneiden")
        elif self.quant == 'ceil':   yq = np.ceil(y)
             # smallest integer i, such that i >= x
        elif self.quant == 'rint':   yq = np.rint(y)
             # round towards nearest int
        elif self.quant == 'none':   yq = y
            # return unquantized value
        else:
            raise Exception('Unknown Requantization type "%s"!'%(self.quant))

        # revert to original fractional scale
        yq = yq * self.LSB
        
        logger.debug("y_in={0} | y={1} | yq={2}".format(y_in, y, yq))

        # Handle Overflow / saturation in relation to MSB
        if   self.ovfl == 'none':
            pass
        else:
            # Bool. vectors with '1' for every neg./pos overflow:
            over_neg = (yq < self.MIN)
            over_pos = (yq >= self.MAX)
            # create flag / array of flags for pos. / neg. overflows
            self.ovr_flag = over_pos.astype(int) - over_neg.astype(int)
            # No. of pos. / neg. / all overflows occured since last reset:
            self.N_over_neg += np.sum(over_neg)
            self.N_over_pos += np.sum(over_pos)
            self.N_over = self.N_over_neg + self.N_over_pos

            # Replace overflows with Min/Max-Values (saturation):
            if self.ovfl == 'sat':
                yq = np.where(over_pos, self.MAX, yq) # (cond, true, false)
                yq = np.where(over_neg, self.MIN, yq)
            # Replace overflows by two's complement wraparound (wrap)
            elif self.ovfl == 'wrap':
                yq = np.where(over_pos | over_neg,
                    yq - 4. * self.MSB*np.fix((np.sign(yq) * 2 * self.MSB+yq)/(4*self.MSB)), yq)
            else:
                raise Exception('Unknown overflow type "%s"!'%(self.ovfl))
                return None

        if scaling == 'div':
            yq = yq / self.scale

        if SCALAR and isinstance(yq, np.ndarray):
            yq = yq.item() # convert singleton array to scalar

        return yq

#------------------------------------------------------------------------------
    def resetN(self):
        """ Reset overflow-counters of Fixed object"""
        self.N_over = 0
        self.N_over_neg = 0
        self.N_over_pos = 0


#------------------------------------------------------------------------------
    def frmt2float(self, y, frmt=None):
        """
        Return floating point representation for fixpoint scalar `y` given in 
        format `frmt`.    
        
        - Construct string representation without radix point, count number of
          fractional places.
        - Calculate integer representation of string, taking the base into account
        (- When result is negative, calculate two's complement for `W` bits)
        - Scale with `2** -W`
        - Scale with the number of fractional places (depending on format!)

        Parameters
        ----------
        y: scalar or string
            to be quantized with the numeric base specified by `frmt`.

        frmt: string (optional)
            any of the formats `float`, `dec`, `bin`, `hex`, `csd`)
            When `frmt` is unspecified, the instance parameter `self.frmt` is used

        Returns
        -------
        floating point (`dtype=np.float64`) representation of fixpoint input.
        """
        csd2dec_vec = np.frompyfunc(csd2dec, 1, 1)

        if y == "":
            return 0

        if frmt is None:
            frmt = self.frmt
        frmt = frmt.lower()

        if frmt == 'float':
            # this handles floats, np scalars + arrays and strings / string arrays
            try:
                y_float = np.float64(y) 
            except ValueError:
                try:
                    y_float = np.complex(y).real
                except Exception as e:
                    y_float = None
                    logger.warning("Can't convert {0}: {1}".format(y,e))
            return y_float

        else: # {'dec', 'bin', 'hex', 'csd'}
         # Find the number of places before the first radix point (if there is one)
         # and join integer and fractional parts
         # when returned string is empty, skip general conversions and rely on error handling
         # of individual routines
            val_str = re.sub(self.FRMT_REGEX[frmt],r'', qstr(y)) # remove illegal characters
            if len(val_str) > 0:

                val_str = val_str.replace(',','.') # ',' -> '.' for German-style numbers
    
                if val_str[0] == '.': # prepend '0' when the number starts with '.'
                    val_str = '0' + val_str
                try:
                    int_str, frc_str = val_str.split('.') # split into integer and fractional places
                except ValueError: # no fractional part
                    int_str = val_str
                    frc_str = ''

                # count number of valid digits in string
                int_places = len(int_str)-1
                frc_places = len(frc_str)
                raw_str = val_str.replace('.','') # join integer and fractional part  
                
                logger.debug("frmt:{0}, int_places={1}".format(frmt, int_places))
                logger.debug("y={0}, val_str={1}, raw_str={2} ".format(y, val_str, raw_str))

            else:
                logger.warning('No valid characters for format {0}!'.format(frmt))
                if fb.data_old is not None:
                    return fb.data_old
                else:
                    return 0


        # (1) calculate the decimal value of the input string using np.float64()
        #     which takes the number of decimal places into account.
        # (2) divide by scale
        if frmt == 'dec':
            # try to convert string -> float directly with decimal point position
            try:
                y_float = self.fixp(val_str, scaling='div')
            except Exception as e:
                logger.warn(e)
                y_float = None

        elif frmt in {'hex', 'bin'}:
            # - Glue integer and fractional part to a string without radix point
            # - Divide by <base> ** <number of fractional places> forcorrect scaling
            # - Transform numbers in negative 2's complement to negative floats.
            #   This is the case when the number is larger than <base> ** (int_places-1)
            # - Calculate the fixpoint representation for correct saturation / quantization
            try:
                y_dec = int(raw_str, self.base) / self.base**frc_places
                # check for negative (two's complement) numbers
                logger.warning("base - frc_places:{0}-{1}".format(self.base, frc_places) )
                if y_dec >=  self.base ** int_places: # (1 << (int_places)):
                    logger.warning("2sComp:{0}-{1}".format(y_dec, 1 << int(np.ceil(np.log2(y_dec)  ))))# / np.log2(self.base)))) )
                    #y_dec = y_dec - (1 << int(np.ceil(np.log2(y_dec) )))# / np.log2(self.base))))
                    y_dec = y_dec - 2 * self.base ** int_places
                # quantize / saturate / wrap & scale the integer value:
                y_float = self.fixp(y_dec, scaling='div')
            except Exception as e:
                logger.warn(e)
                y_dec = None
                y_float = None

            logger.debug("MSB={0} | LSB={1} | scale={2}".format(self.MSB, self.LSB, self.scale))
            logger.debug("y_in={0} | y_dec={1}".format(y, y_dec))

        elif frmt == 'csd':
            y_float = csd2dec(raw_str, int_places)
            if y_float is not None:
                y_float = y_float / 2**(self.W-1)

        else:
            logger.error('Unknown output format "%s"!'.format(frmt))
            y_float = None

        if frmt != "float": logger.debug("MSB={0:g} |  scale={1:g} | "
              "y={2} | y_float={3}".format(self.MSB, self.scale, y, y_float))

        if y_float is not None:
            return y_float
        elif fb.data_old is not None:
            return fb.data_old
        else:
            return 0


#------------------------------------------------------------------------------
    def float2frmt(self, y):
        """
        Called a.o. by `itemDelegate.displayText()` for on-the-fly number 
        conversion. Returns fixpoint representation for `y` (scalar or array-like) 
        with numeric format `self.frmt` and `self.W` bits. The result has the 
        same shape as `y`.

        The float is multiplied by `self.scale` and quantized / saturated 
        using fixp() for all formats before it is converted to different number
        formats.

        Parameters
        ----------
        y: scalar or array-like object (numeric or string) in fractional format
            to be transformed

        Returns
        -------
        A string, a float or an ndarray of float or string is returned in the 
        numeric format set in `self.frmt`. It has the same shape as `y`. For all
        formats except `float` a fixpoint representation with `self.W` binary 
        digits is returned.
        """
        #======================================================================
        # Define vectorized functions for dec -> frmt  using numpys 
        # automatic type casting
        #======================================================================
        """
        Vectorized function for inserting binary point in string `bin_str` 
        after position `pos`.

        Usage:  insert_binary_point(bin_str, pos)

        Parameters: bin_str : string
                    pos     : integer
        """
        insert_binary_point = np.vectorize(lambda bin_str, pos:(
                                    bin_str[:pos+1] + "." + bin_str[pos+1:]))
        
        dec2bin_vec = np.frompyfunc(np.binary_repr, 2, 1)
        dec2csd_vec = np.frompyfunc(dec2csd, 2, 1)
        bin2hex_vec = np.frompyfunc(bin2hex, 2, 1)
        dec2hex_vec = np.frompyfunc(dec2hex, 2, 1)
        #dec2hex_vec = 
        #======================================================================

        if self.frmt == 'float': # return float input value unchanged
            return y

        elif self.frmt in {'hex', 'bin', 'dec', 'csd'}:
            # quantize & treat overflows of y (float), returning a float
            y_fix = self.fixp(y, scaling='mult')
            # logger.debug("y={0} | y_fix={1}".format(y, y_fix))
            if self.frmt == 'dec':
                if self.WF == 0:
                    y_fix = np.int64(y_fix) # get rid of trailing zero

                y_str = str(y_fix) # use fixpoint number as returned by fixp()

            elif self.frmt == 'csd':
                y_str = dec2csd_vec(y_fix, self.WF) # convert with WF fractional bits

            else: # bin or hex
                # represent fixpoint number as integer in the range -2**(W-1) ... 2**(W-1)
                y_fix_int = np.int64(np.round(y_fix / self.LSB))
                # split into fractional and integer part, both represented as integer
                yi = np.round(np.modf(y_fix)[1]).astype(int) # integer part
                yf = np.round(np.modf(y_fix)[0] * (1 << self.WF)).astype(int) # frac part as integer

                if self.frmt == 'hex':
                    if self.WF > 0:
                        y_str_bin_i = np.binary_repr(y_fix_int, self.W)[:self.WI+1]
                        y_str_bin_f = np.binary_repr(y_fix_int, self.W)[self.WI+1:]
                        y_str = bin2hex(y_str_bin_i) + "." + bin2hex(y_str_bin_f, frac=True)
                    else:
                        y_str = dec2hex(yi, self.W)
                else: # self.frmt == 'bin':
                    # calculate binary representation of fixpoint integer
                    y_str = dec2bin_vec(y_fix_int, self.W)

                    if self.WF > 0:
                        # ... and insert the radix point if required
                        y_str = insert_binary_point(y_str, self.WI)

                # logger.debug("yi={0} | yf={1} | y_str={2}".format(yi, yf, y_str))
            if isinstance(y_str, np.ndarray) and np.ndim(y_str) < 1:
                y_str = y_str.item() # convert singleton array to scalar

            return y_str
        else:
            raise Exception('Unknown output format "%s"!'%(self.frmt))
            return None

########################################
# If called directly, do some examples #
########################################
if __name__=='__main__':
    import pprint

    q_obj = {'WI':0, 'WF':3, 'ovfl':'sat', 'quant':'round', 'frmt': 'dec', 'scale': 1}
    myQ = Fixed(q_obj) # instantiate fixpoint object with settings above
    
    y_list = [-1.1, -1.0, -0.5, 0, 0.5, 0.99, 1.0]
    print("W = ", myQ.W, myQ.LSB, myQ.MSB)

    myQ.setQobj(q_obj)

    print("\nTesting float2frmt()\n====================\n")       
    for y in y_list:
        print("y -> y_fix", y, "->", myQ.fixp(y, scaling='mult'))
        print(myQ.frmt, myQ.float2frmt(y))
            
    print("\nTesting frmt2float()\n====================\n")
    q_obj = {'WI':0, 'WF':3, 'ovfl':'sat', 'quant':'round', 'frmt': 'dec'}
    pprint.pprint(q_obj)
    myQ.setQobj(q_obj)
    dec_list = [-9, -8, -7, -4.0, -3.578, 0, 0.5, 4, 7, 8]
    for dec in dec_list:
        print("{0} -> {1} ({2})".format(dec, myQ.frmt2float(dec), myQ.frmt))
