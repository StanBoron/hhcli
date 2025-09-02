import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

/** Плоский элемент словаря */
type AreaItem = { id: string; name: string; parent_id: string | null };

type Props = {
  value?: string; // выбранный city/leaf id
  onChange: (id: string) => void; // отдаём конечный id (город/лист)
  className?: string;
  labels?: { country?: string; region?: string; city?: string };
};

async function fetchAreas(parent?: string | null): Promise<AreaItem[]> {
  if (parent == null) {
    // верхний уровень (страны и крупные регионы)
    const r = await api.get("/dicts/areas", { params: { flat: true } });
    const data = r.data?.data ?? r.data;
    // Возвращаем только корневые
    return (data as AreaItem[]).filter((x) => x.parent_id == null);
  }
  const r = await api.get("/dicts/areas", { params: { parent, flat: true } });
  return r.data?.data ?? r.data;
}

/**
 * Каскад: страна -> регион -> город.
 * Если у уровня нет детей, селект скрывается.
 */
export default function AreaSelect({ value, onChange, className, labels }: Props) {
  const [countryId, setCountryId] = useState<string>("");
  const [regionId, setRegionId] = useState<string>("");
  const [cityId, setCityId] = useState<string>(value ?? "");

  // 1) страны (root)
  const countries = useQuery({
    queryKey: ["areas", "countries"],
    queryFn: () => fetchAreas(null),
    staleTime: 24 * 60 * 60 * 1000,
  });

  // 2) регионы по стране
  const regions = useQuery({
    queryKey: ["areas", "regions", countryId],
    queryFn: () => (countryId ? fetchAreas(countryId) : Promise.resolve<AreaItem[]>([])),
    enabled: !!countryId,
    staleTime: 24 * 60 * 60 * 1000,
  });

  // 3) города по региону
  const cities = useQuery({
    queryKey: ["areas", "cities", regionId],
    queryFn: () => (regionId ? fetchAreas(regionId) : Promise.resolve<AreaItem[]>([])),
    enabled: !!regionId,
    staleTime: 24 * 60 * 60 * 1000,
  });

  // отдать наружу выбранный лист (город) при изменении
  useEffect(() => {
    if (cityId) onChange(cityId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cityId]);

  // Сброс дочерних уровней, когда меняется родитель
  useEffect(() => {
    setRegionId("");
    setCityId("");
  }, [countryId]);
  useEffect(() => {
    setCityId("");
  }, [regionId]);

  // Быстрые плейсхолдеры
  const L = useMemo(
    () => ({
      country: labels?.country ?? "Country",
      region: labels?.region ?? "Region",
      city: labels?.city ?? "City",
    }),
    [labels]
  );

  const rootClass = className ?? "border rounded p-2";
  const disabled = countries.isFetching;

  return (
    <div className="flex gap-2 items-stretch">
      {/* Country */}
      <select
        className={rootClass}
        value={countryId}
        onChange={(e) => setCountryId(e.target.value)}
        disabled={disabled || countries.isError}
        title={countries.isError ? "Failed to load areas" : L.country}
      >
        <option value="">{L.country}</option>
        {(countries.data ?? []).map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>

      {/* Region (показываем только если есть данные/идёт загрузка) */}
      {regions.data?.length || regions.isFetching ? (
        <select
          className={rootClass}
          value={regionId}
          onChange={(e) => setRegionId(e.target.value)}
          disabled={regions.isFetching || regions.isError}
          title={regions.isError ? "Failed to load regions" : L.region}
        >
          <option value="">{L.region}</option>
          {(regions.data ?? []).map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      ) : null}

      {/* City (показываем если есть дети у региона) */}
      {cities.data?.length || cities.isFetching ? (
        <select
          className={rootClass}
          value={cityId}
          onChange={(e) => setCityId(e.target.value)}
          disabled={cities.isFetching || cities.isError}
          title={cities.isError ? "Failed to load cities" : L.city}
        >
          <option value="">{L.city}</option>
          {(cities.data ?? []).map((ct) => (
            <option key={ct.id} value={ct.id}>
              {ct.name}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}
