from typing import List
from typing import Tuple
from typing import Union

import allel
import numpy as np
from allel import AlleleCountsArray
from allel import GenotypeArray
from numpy.typing import NDArray
from scipy import special

from pixy.enums import FSTEstimator
from pixy.models import NA
from pixy.models import DxyResult
from pixy.models import FstResult
from pixy.models import PiResult

# vectorized functions for calculating pi and dxy
# these are reimplementations of the original functions


def count_diff_comp_missing(row: AlleleCountsArray, n_haps: int) -> Tuple[int, int, int]:
    """
    Helper function for the calculation of pi.

    For the given site (row of the count table), count the number of differences, number of
    comparisons, and number of missing. The function uses the number of haploid samples (n_haps) to
    compute the number of expected genotype comparisons and determine the count of missing.

    Args:
        row: counts of each allele at a given site
        n_haps: number of haploid samples in the population

    Returns:
        A tuple `(diffs, comps, missing)`, where `diffs` is the number of differences within the
        population, `comps` is the number of comparisons made within the population, and `missing`
        is the difference between the actual number of comparisons and the total possible (based on
        the number of haploid samples).
    """
    n_gts: int = np.sum(row)  # number of observed genotypes

    # number of possible pairwise comparisons, if all samples are called
    n_possible_comps: int = int(special.comb(N=n_haps, k=2))

    if n_gts == 0:
        # No observed genotypes in the row
        return 0, 0, n_possible_comps

    # Find the highest index of an observed allele, and assume it is the allelism of the site
    # (If the variant technically has other alleles, they zero out anyways)
    observed_alleles: NDArray = np.nonzero(row)[0]
    allelism = np.argmax(observed_alleles) + 1

    comps = int(special.comb(N=n_gts, k=2))  # calculate combinations, return an integer
    missing = n_possible_comps - comps

    # Use shortcut: the number of differences is the sum of all pairwise products of the observed
    # allele counts
    diffs = 0
    for i in range(allelism - 1):
        for j in range(i + 1, allelism):
            diffs += row[i] * row[j]

    return diffs, comps, missing


def calc_pi(gt_array: GenotypeArray) -> PiResult:
    """
    Given a filtered genotype matrix, calculate `pi`.

    This function implements vectorized calculation of pi from a pre-filtered scikit-allel genotype
    matrix. This function does not support filtering of the input by population - it simply
    calculates pi on all of the provided samples.

    Args:
        gt_array: a GenotypeArray representing the calls of each variant at each filtered site in a
            given population. This array must be pre-filtered to the population of interest.

    Returns:
        The average `pi` and total difference, comparison, and missing counts over all sites in the
        input array.
    """
    # counts of each of the two alleles at each site
    allele_counts: AlleleCountsArray = gt_array.count_alleles()

    # the number of (haploid) samples in the population
    n_haps = gt_array.n_samples * gt_array.ploidy

    # compute the number of differences, comparisons, and missing data at each site
    diff_comp_missing_matrix = np.apply_along_axis(
        count_diff_comp_missing, 1, allele_counts, n_haps
    )

    # sum up the above quantities for totals for the region
    diff_comp_missing_sums = np.sum(diff_comp_missing_matrix, 0)

    # extract the component values
    total_diffs = diff_comp_missing_sums[0]
    total_comps = diff_comp_missing_sums[1]
    total_missing = diff_comp_missing_sums[2]

    # alternative method for calculating total_missing
    # produces the same result as original method (included as sanity check)
    # total_possible = ((n_haps * (n_haps-1))/2) * len(allele_counts)
    # total_missing = total_possible - total_comps

    # if there are valid data (comparisons between genotypes) at the site, compute average dxy
    # otherwise return NA
    avg_pi: Union[float, NA] = total_diffs / total_comps if total_comps > 0 else "NA"

    return PiResult(
        avg_pi=avg_pi,
        total_diffs=total_diffs,
        total_comps=total_comps,
        total_missing=total_missing,
    )


# function for vectorized calculation of dxy from a pre-filtered scikit-allel genotype matrix
def calc_dxy(pop1_gt_array: GenotypeArray, pop2_gt_array: GenotypeArray) -> DxyResult:
    """
    Given a filtered genotype matrix, calculate `dxy`.

    Args:
        pop1_gt_array: the GenotypeArray representing population-specific allele counts
        pop2_gt_array: the GenotypeArray representing population-specific allele counts


    Returns:
        avg_dxy: proportion of total differences across total comparisons. "NA" if no valid data.
        total_diffs: sum of the number of differences between the populations
        total_comps: sum of the number of comparisons between the populations
        total_missing: sum of the number of missing between the populations
    """
    if pop1_gt_array.n_variants != pop2_gt_array.n_variants:
        raise ValueError("Input genotype matrices must have the same number of variants")

    n_sites: int = pop1_gt_array.n_variants

    # the counts of each of the two alleles in each population at each site
    pop1_allele_counts: AlleleCountsArray = pop1_gt_array.count_alleles()
    pop2_allele_counts: AlleleCountsArray = pop2_gt_array.count_alleles()

    # the number of (haploid) samples in each population
    pop1_n_haps: int = pop1_gt_array.n_samples * pop1_gt_array.ploidy
    pop2_n_haps: int = pop2_gt_array.n_samples * pop2_gt_array.ploidy

    # Find the highest index of an observed allele, and assume it is the allelism of every site
    # (If a site has fewer alleles, they zero out)
    allelism = np.max([pop1_allele_counts.max_allele(), pop2_allele_counts.max_allele()]) + 1

    # the total number of differences between populations summed across all sites
    persite_diffs: NDArray = np.zeros(n_sites)
    for i in range(allelism):
        for j in range(allelism):
            if i != j:
                persite_diffs += pop1_allele_counts[:, i] * pop2_allele_counts[:, j]

    total_diffs: int = np.sum(persite_diffs)

    # the total number of actual pairwise comparisons between sites, excluding missing calls
    persite_comps: NDArray = np.sum(pop1_allele_counts, axis=1) * np.sum(pop2_allele_counts, axis=1)
    assert persite_comps.shape == (n_sites,)
    total_comps: int = np.sum(persite_comps)

    # the total count of possible pairwise comparisons at all sites
    total_possible: int = (pop1_n_haps * pop2_n_haps) * n_sites

    # the amount of missing is possible comps - actual ('total') comps
    total_missing: int = total_possible - total_comps

    # if there are valid data (comparisons between genotypes) at the site, compute average dxy
    # otherwise return NA
    avg_dxy: Union[float, NA] = total_diffs / total_comps if total_comps > 0 else "NA"

    return DxyResult(
        avg_dxy=avg_dxy,
        total_diffs=total_diffs,
        total_comps=total_comps,
        total_missing=total_missing,
    )


# function for obtaining fst AND variance components via scikit allel function
# (need variance components for proper aggregation)
# for single sites, this is the final FST calculation
# in aggregation mode, we just want a,b,c and n_sites for aggregating and fst
def calc_fst(
    gt_array_fst: GenotypeArray, fst_pop_indicies: List[List[int]], fst_type: FSTEstimator
) -> FstResult:
    # TODO: update the return type here after refactoring (2 -> 1 return statements)
    """
    Calculates FST according to either Weir and Cockerham (1984) or Hudson (1992).

    FST is a measure of the total genetic variance within a subpopulation relative to total genetic
    variance.

    Args:
        gt_array_fst: allele counts to use for computation of variance
        fst_pop_indicies: sample indices for each subpopulation
        fst_type: one of either WC or Hudson, corresponding to the method of calculation

    Returns:
        If `fst_type` is `wc`, the following will be returned:
            fst: "NA" if no valid data
            a: variance between populations
            b: variance between individuals within populations
            c: variance between gametes within individuals
            n_sites: the number of sites over which variance was measured

        The shape of `a`, `b`, and `c` correspond to [n_variants * n_alleles].

        If `fst_type` is `hudson`, the following will be returned:
            fst: "NA" if no valid data.
            num: divergence between the two populations minus average of diversity within
              each population
            den: divergence between the two populations.
            c: a placeholder of 0 to maintain the shape of the return Tuple
            n_sites: the number of sites over which variance was measured

    """
    # compute basic (multisite) FST via scikit allel
    fst: Union[float, NA]
    result: FstResult

    n_sites: int = len(gt_array_fst)

    # WC 84
    if fst_type is FSTEstimator.WC:
        if gt_array_fst.ploidy != 2:
            raise NotImplementedError(
                "`pixy` does not currently support calculation "
                "of Weir-Cockerham FST in non-diploid genomes"
            )
        a: NDArray
        b: NDArray
        c: NDArray
        a, b, c = allel.weir_cockerham_fst(gt_array_fst, subpops=fst_pop_indicies)

        # compute variance component sums
        a_sum: float = np.nansum(a)
        b_sum: float = np.nansum(b)
        c_sum: float = np.nansum(c)

        # compute fst
        if (a_sum + b_sum + c_sum) > 0:
            fst = a_sum / (a_sum + b_sum + c_sum)
        else:
            fst = "NA"

        result = FstResult(fst=fst, a=a_sum, b=b_sum, c=c_sum, n_sites=n_sites)

    # Hudson 92
    if fst_type is FSTEstimator.HUDSON:
        # following scikit allel docs
        # allel counts for each population
        ac1: AlleleCountsArray = gt_array_fst.count_alleles(subpop=fst_pop_indicies[0])
        ac2: AlleleCountsArray = gt_array_fst.count_alleles(subpop=fst_pop_indicies[1])

        # hudson fst has two components (numerator & denominator)
        num: NDArray
        den: NDArray
        num, den = allel.hudson_fst(ac1, ac2)

        # compute variance component sums
        num_sum: float = np.nansum(num)
        den_sum: float = np.nansum(den)

        # compute fst
        if (num_sum + den_sum) > 0:
            fst = num_sum / den_sum
        else:
            fst = "NA"

        # same abc format as WC84, where 'a' is the numerator and
        # 'b' is the demoninator, and 'c' is a zero placeholder
        result = FstResult(fst=fst, a=num_sum, b=den_sum, c=0, n_sites=n_sites)

    return result


# simplified version of above to handle the case
# of per-site estimates of FST over whole chunks


def calc_fst_persite(
    gt_array_fst: GenotypeArray,
    fst_pop_indicies: List[List[int]],
    fst_type: str,
) -> NDArray[np.float64]:
    """
    Calculates site-specific FST according to Weir and Cockerham (1984) or Hudson (1992).

    Args:
        gt_array_fst: allele counts to use for computation of variance
        fst_pop_indicies: sample indices for each subpopulation
        fst_type: one of either WC or Hudson, corresponding to the method of calculation

    Returns:
        fst: the site-specific variance

    """
    # compute basic (multisite) FST via scikit allel
    fst: NDArray[np.float64]

    # WC 84
    if fst_type == "wc":
        a, b, c = allel.weir_cockerham_fst(gt_array_fst, subpops=fst_pop_indicies)

        fst = np.sum(a, axis=1) / (np.sum(a, axis=1) + np.sum(b, axis=1) + np.sum(c, axis=1))

    # Hudson 92
    elif fst_type == "hudson":
        # following scikit allel docs
        # allel counts for each population
        ac1 = gt_array_fst.count_alleles(subpop=fst_pop_indicies[0])
        ac2 = gt_array_fst.count_alleles(subpop=fst_pop_indicies[1])

        # hudson fst has two components (numerator & denominator)
        num, den = allel.hudson_fst(ac1, ac2)

        fst = num / den

    else:
        raise ValueError("fst_type must be either 'wc' or 'hudson'")

    return fst
